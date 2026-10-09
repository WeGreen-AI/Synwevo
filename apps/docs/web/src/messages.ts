import type { Messages } from "@synwevo/ui/i18n-core";

/** Docs interface only. Template contents, values and persisted enum keys are never translated. */
export const messages = {
  "文件尚未通过安全检查": {"en": "File has not passed its security check", "zh-Hant": "文件尚未通過安全檢查"},
  "正在安全检查…": {"en": "Checking file security…", "zh-Hant": "正在安全檢查…"},
  "检查通过后才会解析并显示内容": {"en": "Content is processed and shown only after the check passes.", "zh-Hant": "檢查通過後才會解析並顯示內容"},
  "重试安全检查": {"en": "Retry security check", "zh-Hant": "重試安全檢查"},
  "正在重新排队…": {"en": "Requeuing…", "zh-Hant": "正在重新排隊…"},
  "安全检查已重新排队，文件在检查通过前仍不可使用": {"en": "Security check queued again. The file stays unavailable until it passes.", "zh-Hant": "安全檢查已重新排隊，文件在檢查通過前仍不可使用"},
  "重新开始了文件安全检查": {"en": "restarted the file security check", "zh-Hant": "重新開始了文件安全檢查"},
  "每个文件最多 {size}；按当前安全检查规则接纳。": {"en": "Up to {size} per file, subject to the current security checks.", "zh-Hant": "每個文件最多 {size}；按目前安全檢查規則接納。"},
  "暂时无法读取文件大小上限，上传时仍会按服务器规则检查。": {"en": "The file size limit is temporarily unavailable. The server will still check uploads.", "zh-Hant": "暫時無法讀取文件大小上限，上傳時仍會按伺服器規則檢查。"},
  "正在读取文件大小上限…": {"en": "Loading the file size limit…", "zh-Hant": "正在讀取文件大小上限…"},
  "文件「{name}」超过当前 {size} 上限，请缩小后重新上传。": {"en": "The file “{name}” exceeds the current {size} limit. Reduce its size and upload again.", "zh-Hant": "文件「{name}」超過目前 {size} 上限，請縮小後重新上傳。"},

  "排队中": {
    "en": "Queued",
    "zh-Hant": "排隊中"
  },
  "生成中": {
    "en": "Generating",
    "zh-Hant": "生成中"
  },
  "取消中": {
    "en": "Cancelling",
    "zh-Hant": "取消中"
  },
  "已完成": {
    "en": "Completed",
    "zh-Hant": "已完成"
  },
  "部分成功": {
    "en": "Partially completed",
    "zh-Hant": "部分成功"
  },
  "已取消": {
    "en": "Cancelled",
    "zh-Hant": "已取消"
  },
  "任务失败": {
    "en": "Job failed",
    "zh-Hant": "任務失敗"
  },
  "等待": {
    "en": "Waiting",
    "zh-Hant": "等待"
  },
  "成功": {
    "en": "Success",
    "zh-Hant": "成功"
  },
  "失败": {
    "en": "Failed",
    "zh-Hant": "失敗"
  },
  "已跳过": {
    "en": "Skipped",
    "zh-Hant": "已跳過"
  },
  "未知": {
    "en": "Unknown",
    "zh-Hant": "未知"
  },
  "每行一份": {
    "en": "One per row",
    "zh-Hant": "每行一份"
  },
  "每文件一份": {
    "en": "One per file",
    "zh-Hant": "每文件一份"
  },
  "每组一份": {
    "en": "One per group",
    "zh-Hant": "每組一份"
  },
  "每块一份": {
    "en": "One per block",
    "zh-Hant": "每塊一份"
  },
  "未定义样本": {
    "en": "No sample rule",
    "zh-Hant": "未定義樣本"
  },
  "你在当前工作空间是查看者，只能查看，不能修改。需要编辑时，请联系工作空间的所有者或管理员调整角色。": {
    "en": "You are a viewer in this workspace. To edit, ask the workspace owner or administrator to change your role.",
    "zh-Hant": "你在當前工作空間是查看者，只能查看，不能修改。需要編輯時，請聯繫工作空間的所有者或管理員調整角色。"
  },
  "你在当前工作空间没有下载和导出权限。需要时，请联系工作空间的所有者或管理员开启。": {
    "en": "You cannot download or export in this workspace. Ask the workspace owner or administrator to enable access.",
    "zh-Hant": "你在當前工作空間沒有下載和導出權限。需要時，請聯繫工作空間的所有者或管理員開啓。"
  },
  "更多": {
    "en": "More",
    "zh-Hant": "更多"
  },
  "最近更新": {
    "en": "Recently updated",
    "zh-Hant": "最近更新"
  },
  "创建时间": {
    "en": "Created",
    "zh-Hant": "創建時間"
  },
  "名称": {
    "en": "Name",
    "zh-Hant": "名稱"
  },
  "新建项目": {
    "en": "New project",
    "zh-Hant": "新建項目"
  },
  "项目名称": {
    "en": "Project name",
    "zh-Hant": "項目名稱"
  },
  "新项目": {
    "en": "New project",
    "zh-Hant": "新項目"
  },
  "已归档「{v1}」，可在「显示已归档」中取消": {
    "en": "Archived “{v1}”. Restore it under “Show archived”.",
    "zh-Hant": "已歸檔「{v1}」，可在「顯示已歸檔」中取消"
  },
  "已取消归档「{v1}」": {
    "en": "Restored “{v1}” from archive",
    "zh-Hant": "已取消歸檔「{v1}」"
  },
  "重命名项目": {
    "en": "Rename project",
    "zh-Hant": "重命名項目"
  },
  "删除项目": {
    "en": "Delete project",
    "zh-Hant": "刪除項目"
  },
  "删除": {
    "en": "Delete",
    "zh-Hant": "刪除"
  },
  "已删除": {
    "en": "Deleted",
    "zh-Hant": "已刪除"
  },
  " · 数据映射与模板批量生成": {
    "en": " · Data mapping and batch document generation",
    "zh-Hant": " · 數據映射與模板批量生成"
  },
  "格式能力矩阵": {
    "en": "Format capabilities",
    "zh-Hant": "格式能力矩陣"
  },
  "关于与许可": {
    "en": "About and licenses",
    "zh-Hant": "關於與許可"
  },
  "＋ 新建项目": {
    "en": "＋ New project",
    "zh-Hant": "＋ 新建項目"
  },
  "项目": {
    "en": "Projects",
    "zh-Hant": "項目"
  },
  "搜索名称 / 模板…": {
    "en": "Search names / templates…",
    "zh-Hant": "搜索名稱 / 模板…"
  },
  "搜索项目": {
    "en": "Search projects",
    "zh-Hant": "搜索項目"
  },
  "排序": {
    "en": "Sort",
    "zh-Hant": "排序"
  },
  "排序方式": {
    "en": "Sort by",
    "zh-Hant": "排序方式"
  },
  "当前升序，切换为降序": {
    "en": "Ascending; switch to descending",
    "zh-Hant": "當前升序，切換為降序"
  },
  "当前降序，切换为升序": {
    "en": "Descending; switch to ascending",
    "zh-Hant": "當前降序，切換為升序"
  },
  "切换升序 / 降序": {
    "en": "Switch ascending / descending",
    "zh-Hant": "切換升序 / 降序"
  },
  "↑ 升序": {
    "en": "↑ Ascending",
    "zh-Hant": "↑ 升序"
  },
  "↓ 降序": {
    "en": "↓ Descending",
    "zh-Hant": "↓ 降序"
  },
  "显示已归档": {
    "en": "Show archived",
    "zh-Hant": "顯示已歸檔"
  },
  "项目列表加载失败：": {
    "en": "Could not load projects: ",
    "zh-Hant": "項目列表加載失敗："
  },
  "重试": {
    "en": "Retry",
    "zh-Hant": "重試"
  },
  "没有项目": {
    "en": "No projects",
    "zh-Hant": "沒有項目"
  },
  "还没有项目": {
    "en": "No projects yet",
    "zh-Hant": "還沒有項目"
  },
  "新建第一个项目": {
    "en": "Create your first project",
    "zh-Hant": "新建第一個項目"
  },
  "加载项目…": {
    "en": "Loading projects…",
    "zh-Hant": "加載項目…"
  },
  "项目列表": {
    "en": "Project list",
    "zh-Hant": "項目列表"
  },
  "模板": {
    "en": "Template",
    "zh-Hant": "模板"
  },
  "来源": {
    "en": "Source",
    "zh-Hant": "來源"
  },
  "样本规则": {
    "en": "Sample rules",
    "zh-Hant": "樣本規則"
  },
  "最近任务": {
    "en": "Latest job",
    "zh-Hant": "最近任務"
  },
  "更新": {
    "en": "Updated",
    "zh-Hant": "更新"
  },
  "操作": {
    "en": "Actions",
    "zh-Hant": "操作"
  },
  "项目（↑↓ 移动，回车打开）": {
    "en": "Projects (↑↓ to move, Enter to open)",
    "zh-Hant": "項目（↑↓ 移動，回車打開）"
  },
  "已归档": {
    "en": "Archived",
    "zh-Hant": "已歸檔"
  },
  "未设置": {
    "en": "Not set",
    "zh-Hant": "未設置"
  },
  "项目「{v1}」的操作": {
    "en": "Actions for project “{v1}”",
    "zh-Hant": "項目「{v1}」的操作"
  },
  "打开": {
    "en": "Open",
    "zh-Hant": "打開"
  },
  "重命名…": {
    "en": "Rename…",
    "zh-Hant": "重命名…"
  },
  "取消归档": {
    "en": "Restore from archive",
    "zh-Hant": "取消歸檔"
  },
  "归档（可撤销）": {
    "en": "Archive (reversible)",
    "zh-Hant": "歸檔（可撤銷）"
  },
  "删除…": {
    "en": "Delete…",
    "zh-Hant": "刪除…"
  },
  "没有匹配「": {
    "en": "No projects matching “",
    "zh-Hant": "沒有匹配「"
  },
  "」的项目": {
    "en": "”",
    "zh-Hant": "」的項目"
  },
  "第 {v1}–{v2} 个，共 {v3} 个项目": {
    "en": "Projects {v1}–{v2} of {v3}",
    "zh-Hant": "第 {v1}–{v2} 個，共 {v3} 個項目"
  },
  "共 {v1} 个项目": {
    "en": "{v1} projects",
    "zh-Hant": "共 {v1} 個項目"
  },
  " · 加载中…": {
    "en": " · Loading…",
    "zh-Hant": " · 加載中…"
  },
  " · 列表内可用 ↑↓ 移动、回车打开": {
    "en": " · Use ↑↓ to move and Enter to open",
    "zh-Hant": " · 列表內可用 ↑↓ 移動、回車打開"
  },
  "‹ 上一页": {
    "en": "‹ Previous",
    "zh-Hant": "‹ 上一頁"
  },
  "下一页 ›": {
    "en": "Next ›",
    "zh-Hant": "下一頁 ›"
  },
  "模板库": {
    "en": "Template library",
    "zh-Hant": "模板庫"
  },
  "模板库加载失败：": {
    "en": "Could not load templates: ",
    "zh-Hant": "模板庫加載失敗："
  },
  "格式": {
    "en": "Format",
    "zh-Hant": "格式"
  },
  "字段": {
    "en": "Fields",
    "zh-Hant": "字段"
  },
  "内置文档编辑器": {
    "en": "Built-in document editor",
    "zh-Hant": "內置文檔編輯器"
  },
  "内置表格编辑器": {
    "en": "Built-in spreadsheet editor",
    "zh-Hant": "內置表格編輯器"
  },
  "编辑": {
    "en": "Edit",
    "zh-Hant": "編輯"
  },
  "模板「{v1}」的操作": {
    "en": "Actions for template “{v1}”",
    "zh-Hant": "模板「{v1}」的操作"
  },
  "复制": {
    "en": "Duplicate",
    "zh-Hant": "複製"
  },
  "已复制": {
    "en": "Duplicated",
    "zh-Hant": "已複製"
  },
  "重命名模板": {
    "en": "Rename template",
    "zh-Hant": "重命名模板"
  },
  "删除模板": {
    "en": "Delete template",
    "zh-Hant": "刪除模板"
  },
  "删除模板「{v1}」？使用它的项目需要重新选择模板。": {
    "en": "Delete template “{v1}”? Projects using it will need a new template.",
    "zh-Hant": "刪除模板「{v1}」？使用它的項目需要重新選擇模板。"
  },
  "已删除模板": {
    "en": "Template deleted",
    "zh-Hant": "已刪除模板"
  },
  "加载模板…": {
    "en": "Loading templates…",
    "zh-Hant": "加載模板…"
  },
  "单值": {
    "en": "Single value",
    "zh-Hant": "單值"
  },
  "固定区域": {
    "en": "Fixed region",
    "zh-Hant": "固定區域"
  },
  "重复明细": {
    "en": "Repeating detail",
    "zh-Hant": "重復明細"
  },
  "文字": {
    "en": "Text",
    "zh-Hant": "文字"
  },
  "单元格": {
    "en": "Cell",
    "zh-Hant": "單元格"
  },
  "重复明细行": {
    "en": "Repeating detail row",
    "zh-Hant": "重復明細行"
  },
  "PDF 区域": {
    "en": "PDF region",
    "zh-Hant": "PDF 區域"
  },
  "PDF 表单字段": {
    "en": "PDF form field",
    "zh-Hant": "PDF 表單字段"
  },
  "幻灯片文字": {
    "en": "Slide text",
    "zh-Hant": "幻燈片文字"
  },
  "（第 {v1} 个同名列）": {
    "en": " (occurrence {v1} of this column name)",
    "zh-Hant": "（第 {v1} 個同名列）"
  },
  "列「{v1}」{v2}": {
    "en": "Column “{v1}”{v2}",
    "zh-Hant": "列「{v1}」{v2}"
  },
  "（标签「{v1}」）": {
    "en": " (label “{v1}”)",
    "zh-Hant": "（標籤「{v1}」）"
  },
  "，至第 {v1} 行": {
    "en": ", through row {v1}",
    "zh-Hant": "，至第 {v1} 行"
  },
  "，至末尾": {
    "en": ", through the end",
    "zh-Hant": "，至末尾"
  },
  "{v1} 明细表（表头第 {v2} 行{v3}）": {
    "en": "{v1} detail table (header row {v2}{v3})",
    "zh-Hant": "{v1} 明細表（表頭第 {v2} 行{v3}）"
  },
  "「…{v1}」后": {
    "en": "after “…{v1}”",
    "zh-Hant": "「…{v1}」後"
  },
  "段首": {
    "en": "paragraph start",
    "zh-Hant": "段首"
  },
  "文档文字 {v1}": {
    "en": "Document text {v1}",
    "zh-Hant": "文檔文字 {v1}"
  },
  "右侧": {
    "en": "to the right",
    "zh-Hant": "右側"
  },
  "下方": {
    "en": "below",
    "zh-Hant": "下方"
  },
  " 第{v1}行第{v2}列": {
    "en": " row {v1}, column {v2}",
    "zh-Hant": " 第{v1}行第{v2}列"
  },
  "文档表格单元格{v1}": {
    "en": "Document table cell{v1}",
    "zh-Hant": "文檔表格單元格{v1}"
  },
  "文档表格明细（表头：{v1}）": {
    "en": "Document detail table (headers: {v1})",
    "zh-Hant": "文檔表格明細（表頭：{v1}）"
  },
  "文档表格区域": {
    "en": "Document table region",
    "zh-Hant": "文檔表格區域"
  },
  "PDF 第 {v1} 页区域{v2}": {
    "en": "PDF page {v1} region{v2}",
    "zh-Hant": "PDF 第 {v1} 頁區域{v2}"
  },
  "扫描第 {v1} 页区域（OCR）": {
    "en": "Scanned page {v1} region (OCR)",
    "zh-Hant": "掃描第 {v1} 頁區域（OCR）"
  },
  "第 {v1} 行文字": {
    "en": "Text on line {v1}",
    "zh-Hant": "第 {v1} 行文字"
  },
  "第 {v1} 张幻灯片「{v2}」": {
    "en": "Slide {v1} “{v2}”",
    "zh-Hant": "第 {v1} 張幻燈片「{v2}」"
  },
  "固定文字「{v1}」": {
    "en": "Fixed text “{v1}”",
    "zh-Hant": "固定文字「{v1}」"
  },
  "来源文件名": {
    "en": "Source filename",
    "zh-Hant": "來源文件名"
  },
  "样本序号": {
    "en": "Sample index",
    "zh-Hant": "樣本序號"
  },
  "分组编号": {
    "en": "Group key",
    "zh-Hant": "分組編號"
  },
  "工作表名": {
    "en": "Sheet name",
    "zh-Hant": "工作表名"
  },
  "固定值": {
    "en": "Fixed value",
    "zh-Hant": "固定值"
  },
  "随分组变化": {
    "en": "Varies by group",
    "zh-Hant": "隨分組變化"
  },
  "随样本变化": {
    "en": "Varies by sample",
    "zh-Hant": "隨樣本變化"
  },
  "固定值（同一文件内相同）": {
    "en": "Fixed value (same within a file)",
    "zh-Hant": "固定值（同一文件內相同）"
  },
  "随文件变化": {
    "en": "Varies by file",
    "zh-Hant": "隨文件變化"
  },
  "（{v1} 行 × {v2} 列）": {
    "en": " ({v1} rows × {v2} columns)",
    "zh-Hant": "（{v1} 行 × {v2} 列）"
  },
  "插入位置（光标）": {
    "en": "Insertion point (cursor)",
    "zh-Hant": "插入位置（光標）"
  },
  "文字「{v1}」": {
    "en": "Text “{v1}”",
    "zh-Hant": "文字「{v1}」"
  },
  "连续 {v1} 个段落「{v2}」": {
    "en": "{v1} consecutive paragraphs “{v2}”",
    "zh-Hant": "連續 {v1} 個段落「{v2}」"
  },
  "表格单元格（第 {v1} 行第 {v2} 列）": {
    "en": "Table cell (row {v1}, column {v2})",
    "zh-Hant": "表格單元格（第 {v1} 行第 {v2} 列）"
  },
  "表格第 {v1} 行": {
    "en": "Table row {v1}",
    "zh-Hant": "表格第 {v1} 行"
  },
  "表格区域 第{v1}-{v2}行 × 第{v3}-{v4}列": {
    "en": "Table region, rows {v1}–{v2} × columns {v3}–{v4}",
    "zh-Hant": "表格區域 第{v1}-{v2}行 × 第{v3}-{v4}列"
  },
  "整个表格（表头 {v1} 行）": {
    "en": "Entire table ({v1} header rows)",
    "zh-Hant": "整個表格（表頭 {v1} 行）"
  },
  "第 {v1} 页框选区域": {
    "en": "Selected region on page {v1}",
    "zh-Hant": "第 {v1} 頁框選區域"
  },
  "表单字段「{v1}」": {
    "en": "Form field “{v1}”",
    "zh-Hant": "表單字段「{v1}」"
  },
  "第 {v1} 行「{v2}」": {
    "en": "Line {v1} “{v2}”",
    "zh-Hant": "第 {v1} 行「{v2}」"
  },
  "幻灯片 {v1} 文字「{v2}」": {
    "en": "Slide {v1} text “{v2}”",
    "zh-Hant": "幻燈片 {v1} 文字「{v2}」"
  },
  "幻灯片 {v1} 表格单元格": {
    "en": "Slide {v1} table cell",
    "zh-Hant": "幻燈片 {v1} 表格單元格"
  },
  "幻灯片 {v1} 表格第 {v2} 行": {
    "en": "Slide {v1} table row {v2}",
    "zh-Hant": "幻燈片 {v1} 表格第 {v2} 行"
  },
  "无法显示表格": {
    "en": "Cannot display spreadsheet",
    "zh-Hant": "無法顯示表格"
  },
  "加载中…": {
    "en": "Loading…",
    "zh-Hant": "加載中…"
  },
  "公式 {v1}": {
    "en": "Formula {v1}",
    "zh-Hant": "公式 {v1}"
  },
  "定位映射": {
    "en": "Locate mapping",
    "zh-Hant": "定位映射"
  },
  "已选 {v1}!{v2}": {
    "en": "Selected {v1}!{v2}",
    "zh-Hant": "已選 {v1}!{v2}"
  },
  "。方向键移动选区，Shift+方向键扩展选区": {
    "en": ". Arrow keys move the selection; Shift+arrows extend it",
    "zh-Hant": "。方向鍵移動選區，Shift+方向鍵擴展選區"
  },
  "工作表「{v1}」，{v2} 行 × {v3} 列{v4}": {
    "en": "Sheet “{v1}”, {v2} rows × {v3} columns{v4}",
    "zh-Hant": "工作表「{v1}」，{v2} 行 × {v3} 列{v4}"
  },
  "表头行": {
    "en": "Header row",
    "zh-Hant": "表頭行"
  },
  "工作表": {
    "en": "Worksheet",
    "zh-Hant": "工作表"
  },
  "{v1} 行 × {v2} 列": {
    "en": "{v1} rows × {v2} columns",
    "zh-Hant": "{v1} 行 × {v2} 列"
  },
  "（隐藏）": {
    "en": "(hidden)",
    "zh-Hant": "（隱藏）"
  },
  "明细「{v1}」的列「{v2}」": {
    "en": "Column “{v2}” of detail “{v1}”",
    "zh-Hant": "明細「{v1}」的列「{v2}」"
  },
  "字段：{v1}": {
    "en": "Field: {v1}",
    "zh-Hant": "字段：{v1}"
  },
  "图片": {
    "en": "Image",
    "zh-Hant": "圖片"
  },
  "嵌入对象": {
    "en": "Embedded object",
    "zh-Hant": "嵌入對象"
  },
  "公式": {
    "en": "Formula",
    "zh-Hant": "公式"
  },
  "对象": {
    "en": "Object",
    "zh-Hant": "對象"
  },
  "插入位置：{v1}": {
    "en": "Insertion point: {v1}",
    "zh-Hant": "插入位置：{v1}"
  },
  "空字段": {
    "en": "Empty field",
    "zh-Hant": "空字段"
  },
  "重复内容块：每条明细复制一次这些内容": {
    "en": "Repeating block: copy this content once per detail row",
    "zh-Hant": "重復內容塊：每條明細複製一次這些內容"
  },
  "重复内容块": {
    "en": "Repeating block",
    "zh-Hant": "重復內容塊"
  },
  "选择整个表格（作为明细表）": {
    "en": "Select entire table (as detail table)",
    "zh-Hant": "選擇整個表格（作為明細表）"
  },
  "选择整个表格": {
    "en": "Select entire table",
    "zh-Hant": "選擇整個表格"
  },
  "选择整行（可设为重复明细行）": {
    "en": "Select entire row (can become a repeating detail row)",
    "zh-Hant": "選擇整行（可設為重復明細行）"
  },
  "选择表格第 {v1} 行": {
    "en": "Select table row {v1}",
    "zh-Hant": "選擇表格第 {v1} 行"
  },
  "从此行开始作为明细表（此行为表头）": {
    "en": "Use this row as the detail table header",
    "zh-Hant": "從此行開始作為明細表（此行為表頭）"
  },
  "以第 {v1} 行为表头选择明细表": {
    "en": "Select detail table with header at row {v1}",
    "zh-Hant": "以第 {v1} 行為表頭選擇明細表"
  },
  "收起数值框选": {
    "en": "Hide numeric selection",
    "zh-Hant": "收起數值框選"
  },
  "用数值框选区域…": {
    "en": "Select region by coordinates…",
    "zh-Hant": "用數值框選區域…"
  },
  "第 {v1} 页": {
    "en": "Page {v1}",
    "zh-Hant": "第 {v1} 頁"
  },
  "{v1}（置信度 {v2}）": {
    "en": "{v1} (confidence {v2})",
    "zh-Hant": "{v1}（置信度 {v2}）"
  },
  " · 旋转 {v1}°": {
    "en": " · Rotated {v1}°",
    "zh-Hant": " · 旋轉 {v1}°"
  },
  " · 扫描页": {
    "en": " · Scanned page",
    "zh-Hant": " · 掃描頁"
  },
  " 文字层": {
    "en": " Text layer",
    "zh-Hant": " 文字層"
  },
  "左": {
    "en": "Left",
    "zh-Hant": "左"
  },
  "上": {
    "en": "Top",
    "zh-Hant": "上"
  },
  "右": {
    "en": "Right",
    "zh-Hant": "右"
  },
  "下": {
    "en": "Bottom",
    "zh-Hant": "下"
  },
  "用数值框选区域": {
    "en": "Select region by coordinates",
    "zh-Hant": "用數值框選區域"
  },
  "页": {
    "en": "Page",
    "zh-Hant": "頁"
  },
  "选中此区域": {
    "en": "Select this region",
    "zh-Hant": "選中此區域"
  },
  "右须大于左、下须大于上，数值 0–100": {
    "en": "Right must exceed left, bottom must exceed top; values range from 0 to 100",
    "zh-Hant": "右須大於左、下須大於上，數值 0–100"
  },
  "选择整行": {
    "en": "Select entire row",
    "zh-Hant": "選擇整行"
  },
  "正在解析文件…": {
    "en": "Parsing file…",
    "zh-Hant": "正在解析文件…"
  },
  "解析完成后自动显示": {
    "en": "The preview appears when parsing finishes",
    "zh-Hant": "解析完成後自動顯示"
  },
  "无法读取此文件": {
    "en": "Cannot read this file",
    "zh-Hant": "無法讀取此文件"
  },
  "无法预览": {
    "en": "Preview unavailable",
    "zh-Hant": "無法預覽"
  },
  "命令面板": {
    "en": "Command palette",
    "zh-Hant": "命令面板"
  },
  "搜索命令…（↑↓ 选择，回车执行，Esc 关闭）": {
    "en": "Search commands… (↑↓ to select, Enter to run, Esc to close)",
    "zh-Hant": "搜索命令…（↑↓ 選擇，回車執行，Esc 關閉）"
  },
  "搜索命令": {
    "en": "Search commands",
    "zh-Hant": "搜索命令"
  },
  "命令": {
    "en": "Command",
    "zh-Hant": "命令"
  },
  "没有匹配的命令": {
    "en": "No matching commands",
    "zh-Hant": "沒有匹配的命令"
  },
  "⌘K / Ctrl+K 打开 · ? 查看全部快捷键": {
    "en": "⌘K / Ctrl+K to open · ? for all shortcuts",
    "zh-Hant": "⌘K / Ctrl+K 打開 · ? 查看全部快捷鍵"
  },
  "全局": {
    "en": "Global",
    "zh-Hant": "全局"
  },
  "命令面板（所有操作）": {
    "en": "Command palette (all actions)",
    "zh-Hant": "命令面板（所有操作）"
  },
  "显示本帮助（单键快捷键）": {
    "en": "Show this help (single-key shortcut)",
    "zh-Hant": "顯示本幫助（單鍵快捷鍵）"
  },
  "撤销上一步（映射、模板字段）": {
    "en": "Undo last action (mappings, template fields)",
    "zh-Hant": "撤銷上一步（映射、模板字段）"
  },
  "重做": {
    "en": "Redo",
    "zh-Hant": "重做"
  },
  "清除选择 / 关闭对话框": {
    "en": "Clear selection / close dialog",
    "zh-Hant": "清除選擇 / 關閉對話框"
  },
  "在当前来源文件中查找": {
    "en": "Find in the current source file",
    "zh-Hant": "在當前來源文件中查找"
  },
  "映射列表（列表获得焦点时）": {
    "en": "Mapping list (when focused)",
    "zh-Hant": "映射列表（列表獲得焦點時）"
  },
  "在映射之间移动": {
    "en": "Move between mappings",
    "zh-Hant": "在映射之間移動"
  },
  "编辑当前映射": {
    "en": "Edit current mapping",
    "zh-Hant": "編輯當前映射"
  },
  "X / 空格": {
    "en": "X / Space",
    "zh-Hant": "X / 空格"
  },
  "选中 / 取消选中（可多选）": {
    "en": "Select / deselect (multiple selection)",
    "zh-Hant": "選中 / 取消選中（可多選）"
  },
  "连续范围多选": {
    "en": "Extend selection range",
    "zh-Hant": "連續範圍多選"
  },
  "全选映射": {
    "en": "Select all mappings",
    "zh-Hant": "全選映射"
  },
  "启用 / 禁用选中的映射": {
    "en": "Enable / disable selected mappings",
    "zh-Hant": "啓用 / 禁用選中的映射"
  },
  "删除选中的映射（可撤销）": {
    "en": "Delete selected mappings (undoable)",
    "zh-Hant": "刪除選中的映射（可撤銷）"
  },
  "取消多选": {
    "en": "Clear multiple selection",
    "zh-Hant": "取消多選"
  },
  "文档与表格": {
    "en": "Documents and spreadsheets",
    "zh-Hant": "文檔與表格"
  },
  "拖动 / Shift+点击": {
    "en": "Drag / Shift+click",
    "zh-Hant": "拖動 / Shift+點擊"
  },
  "选择文字、单元格区域": {
    "en": "Select text or a cell range",
    "zh-Hant": "選擇文字、單元格區域"
  },
  "方向键 / Shift+方向键": {
    "en": "Arrow keys / Shift+arrows",
    "zh-Hant": "方向鍵 / Shift+方向鍵"
  },
  "表格获得焦点时移动 / 扩展选区": {
    "en": "Move / extend selection when the grid is focused",
    "zh-Hant": "表格獲得焦點時移動 / 擴展選區"
  },
  "Alt/Option + 点击": {
    "en": "Alt/Option + click",
    "zh-Hant": "Alt/Option + 點擊"
  },
  "在文档中放置插入光标（不替换原文）": {
    "en": "Place an insertion cursor in the document (keep original text)",
    "zh-Hant": "在文檔中放置插入光標（不替換原文）"
  },
  "来源文件列表：Alt/Option + ↑ / ↓": {
    "en": "Source list: Alt/Option + ↑ / ↓",
    "zh-Hant": "來源文件列表：Alt/Option + ↑ / ↓"
  },
  "调整当前文件的顺序": {
    "en": "Reorder the current file",
    "zh-Hant": "調整當前文件的順序"
  },
  "分隔条：← / →（Shift 加大步长）": {
    "en": "Divider: ← / → (Shift for larger steps)",
    "zh-Hant": "分隔條：← / →（Shift 加大步長）"
  },
  "调整宽度；中间分隔条 Enter 恢复默认，右侧面板分隔条 Enter 收起": {
    "en": "Adjust width; Enter resets the middle divider or collapses the side panel",
    "zh-Hant": "調整寬度；中間分隔條 Enter 恢復默認，右側面板分隔條 Enter 收起"
  },
  "生成": {
    "en": "Generate",
    "zh-Hant": "生成"
  },
  "开始批量生成": {
    "en": "Start batch generation",
    "zh-Hant": "開始批量生成"
  },
  "打开 / 关闭预览（单键快捷键）": {
    "en": "Open / close preview (single-key shortcut)",
    "zh-Hant": "打開 / 關閉預覽（單鍵快捷鍵）"
  },
  "键盘快捷键": {
    "en": "Keyboard shortcuts",
    "zh-Hant": "鍵盤快捷鍵"
  },
  "输入框、编辑器与中文输入法输入时，字母快捷键不会触发。": {
    "en": "Letter shortcuts are inactive in inputs, editors and while composing with an input method.",
    "zh-Hant": "輸入框、編輯器與中文輸入法輸入時，字母快捷鍵不會觸發。"
  },
  "知道了": {
    "en": "Got it",
    "zh-Hant": "知道了"
  },
  "启用单键快捷键（P、?）。使用语音输入或经常误触时可以关闭；带 ⌘ / Ctrl 的快捷键不受影响。": {
    "en": "Enable single-key shortcuts (P, ?). Disable for voice input or accidental activation. ⌘ / Ctrl shortcuts remain available.",
    "zh-Hant": "啓用單鍵快捷鍵（P、?）。使用語音輸入或經常誤觸時可以關閉；帶 ⌘ / Ctrl 的快捷鍵不受影響。"
  },
  "{v1}失败：{v2}": {
    "en": "{v1} failed: {v2}",
    "zh-Hant": "{v1}失敗：{v2}"
  },
  "调整顺序": {
    "en": "Reorder",
    "zh-Hant": "調整順序"
  },
  "「{v1}」已移到第 {v2} 位，共 {v3} 个文件": {
    "en": "“{v1}” moved to position {v2} of {v3} files",
    "zh-Hant": "「{v1}」已移到第 {v2} 位，共 {v3} 個文件"
  },
  "展开文件列表": {
    "en": "Expand file list",
    "zh-Hant": "展開文件列表"
  },
  "展开来源文件列表": {
    "en": "Expand source file list",
    "zh-Hant": "展開來源文件列表"
  },
  "来源文件 ": {
    "en": "Source files ",
    "zh-Hant": "來源文件 "
  },
  "收起": {
    "en": "Collapse",
    "zh-Hant": "收起"
  },
  "收起来源文件列表": {
    "en": "Collapse source file list",
    "zh-Hant": "收起來源文件列表"
  },
  "＋ 拖入或点击上传": {
    "en": "＋ Drop files here or click to upload",
    "zh-Hant": "＋ 拖入或點擊上傳"
  },
  "可多选：XLSX/CSV/DOCX/PDF/图片/PPTX 等": {
    "en": "Select multiple: XLSX/CSV/DOCX/PDF/images/PPTX, etc.",
    "zh-Hant": "可多選：XLSX/CSV/DOCX/PDF/圖片/PPTX 等"
  },
  "上传 {v1}": {
    "en": "Upload {v1}",
    "zh-Hant": "上傳 {v1}"
  },
  "拖动调整顺序（也可用下方的上移 / 下移，或 Alt+↑ / ↓）": {
    "en": "Drag to reorder (or use Move up / down, or Alt+↑ / ↓)",
    "zh-Hant": "拖動調整順序（也可用下方的上移 / 下移，或 Alt+↑ / ↓）"
  },
  "，代表文件": {
    "en": ", representative file",
    "zh-Hant": "，代表文件"
  },
  "{v1}，第 {v2} 个，共 {v3} 个{v4}": {
    "en": "{v1}, file {v2} of {v3}{v4}",
    "zh-Hant": "{v1}，第 {v2} 個，共 {v3} 個{v4}"
  },
  "用于建立规则的代表文件": {
    "en": "Representative file used to define rules",
    "zh-Hant": "用於建立規則的代表文件"
  },
  "代表": {
    "en": "Representative",
    "zh-Hant": "代表"
  },
  "上移「{v1}」": {
    "en": "Move “{v1}” up",
    "zh-Hant": "上移「{v1}」"
  },
  "上移（Alt+↑）": {
    "en": "Move up (Alt+↑)",
    "zh-Hant": "上移（Alt+↑）"
  },
  "↑ 上移": {
    "en": "↑ Move up",
    "zh-Hant": "↑ 上移"
  },
  "下移「{v1}」": {
    "en": "Move “{v1}” down",
    "zh-Hant": "下移「{v1}」"
  },
  "下移（Alt+↓）": {
    "en": "Move down (Alt+↓)",
    "zh-Hant": "下移（Alt+↓）"
  },
  "↓ 下移": {
    "en": "↓ Move down",
    "zh-Hant": "↓ 下移"
  },
  "设为代表文件": {
    "en": "Set as representative file",
    "zh-Hant": "設為代表文件"
  },
  "设为代表": {
    "en": "Set as representative",
    "zh-Hant": "設為代表"
  },
  "下载原文件（未修改）": {
    "en": "Download original (unchanged)",
    "zh-Hant": "下載原文件（未修改）"
  },
  "原件": {
    "en": "Original",
    "zh-Hant": "原件"
  },
  "无下载权限": {
    "en": "Download unavailable",
    "zh-Hant": "無下載權限"
  },
  "移除来源文件": {
    "en": "Remove source file",
    "zh-Hant": "移除來源文件"
  },
  "从项目中移除「{v1}」？原文件本身不受影响；已生成的历史结果仍保留。": {
    "en": "Remove “{v1}” from this project? The original file and previously generated results are retained.",
    "zh-Hant": "從項目中移除「{v1}」？原文件本身不受影響；已生成的歷史結果仍保留。"
  },
  "移除": {
    "en": "Remove",
    "zh-Hant": "移除"
  },
  "还没有来源文件": {
    "en": "No source files yet",
    "zh-Hant": "還沒有來源文件"
  },
  "{序号}": {
    "en": "{序号}",
    "zh-Hant": "{序號}"
  },
  "（例：{v1}：{v2}）": {
    "en": " (example: {v1}: {v2})",
    "zh-Hant": "（例：{v1}：{v2}）"
  },
  "批量生成": {
    "en": "Batch generation",
    "zh-Hant": "批量生成"
  },
  "取消": {
    "en": "Cancel",
    "zh-Hant": "取消"
  },
  "正在创建任务…": {
    "en": "Creating job…",
    "zh-Hant": "正在創建任務…"
  },
  "开始生成 {v1} 个文件": {
    "en": "Generate {v1} files",
    "zh-Hant": "開始生成 {v1} 個文件"
  },
  "等待样本检查": {
    "en": "Waiting for sample checks",
    "zh-Hant": "等待樣本檢查"
  },
  "正在检查当前设置下的所有样本…": {
    "en": "Checking all samples with the current settings…",
    "zh-Hant": "正在檢查當前設置下的所有樣本…"
  },
  "输入文件（可用 ": {
    "en": "Input files (ready: ",
    "zh-Hant": "輸入文件（可用 "
  },
  "识别样本": {
    "en": "Detected samples",
    "zh-Hant": "識別樣本"
  },
  "有效样本": {
    "en": "Valid samples",
    "zh-Hant": "有效樣本"
  },
  "有错误的样本": {
    "en": "Samples with errors",
    "zh-Hant": "有錯誤的樣本"
  },
  "警告": {
    "en": "Warnings",
    "zh-Hant": "警告"
  },
  " 个样本存在错误": {
    "en": " samples have errors",
    "zh-Hant": " 個樣本存在錯誤"
  },
  "输出格式": {
    "en": "Output format",
    "zh-Hant": "輸出格式"
  },
  "不可用": {
    "en": "Unavailable",
    "zh-Hant": "不可用"
  },
  "分页检查": {
    "en": "Pagination checks",
    "zh-Hant": "分頁檢查"
  },
  "不检查（最快）": {
    "en": "Skip checks (fastest)",
    "zh-Hant": "不檢查（最快）"
  },
  "保持模板页数：先缩小允许缩小的字段，仍超出则该份失败": {
    "en": "Keep template page count: shrink eligible fields; fail if still too long",
    "zh-Hant": "保持模板頁數：先縮小允許縮小的字段，仍超出則該份失敗"
  },
  "新内容更长时分页可能变化；可在预览中打开「真实分页」查看。": {
    "en": "Longer content can change pagination. Use “Actual pagination” in the preview to check.",
    "zh-Hant": "新內容更長時分頁可能變化；可在預覽中打開「真實分頁」查看。"
  },
  "模板中还没有字段勾选「超长时允许缩小字号」：超出页数的样本会直接失败。": {
    "en": "No fields allow font shrinking. Samples exceeding the template page count will fail.",
    "zh-Hant": "模板中還沒有字段勾選「超長時允許縮小字號」：超出頁數的樣本會直接失敗。"
  },
  "文件命名": {
    "en": "Filenames",
    "zh-Hant": "文件命名"
  },
  "文件命名规则": {
    "en": "Filename pattern",
    "zh-Hant": "文件命名規則"
  },
  "插入 {{v1}}": {
    "en": "Insert {{v1}}",
    "zh-Hant": "插入 {{v1}}"
  },
  "示例：": {
    "en": "Example: ",
    "zh-Hant": "示例："
  },
  "非法字符会被替换，过长会截断；重名自动加序号「(2)」，不会覆盖。": {
    "en": "Invalid characters are replaced and long names shortened. Duplicates receive a suffix such as “(2)” without overwriting.",
    "zh-Hant": "非法字符會被替換，過長會截斷；重名自動加序號「(2)」，不會覆蓋。"
  },
  "遇到错误时": {
    "en": "On error",
    "zh-Hant": "遇到錯誤時"
  },
  "有错误则不开始（默认）": {
    "en": "Do not start if errors exist (default)",
    "zh-Hant": "有錯誤則不開始（默認）"
  },
  "先修正所有错误样本": {
    "en": "Fix all sample errors first",
    "zh-Hant": "先修正所有錯誤樣本"
  },
  "跳过错误样本，生成其余样本": {
    "en": "Skip invalid samples and generate the rest",
    "zh-Hant": "跳過錯誤樣本，生成其餘樣本"
  },
  "跳过的样本会逐条列在结果清单中": {
    "en": "Skipped samples are individually listed in the manifest",
    "zh-Hant": "跳過的樣本會逐條列在結果清單中"
  },
  "缺少列": {
    "en": "Missing column",
    "zh-Hant": "缺少列"
  },
  "重名列": {
    "en": "Duplicate column name",
    "zh-Hant": "重名列"
  },
  "缺少工作表": {
    "en": "Missing worksheet",
    "zh-Hant": "缺少工作表"
  },
  "工作表歧义": {
    "en": "Ambiguous worksheet",
    "zh-Hant": "工作表歧義"
  },
  "找不到定位标签": {
    "en": "Anchor label not found",
    "zh-Hant": "找不到定位標籤"
  },
  "定位标签不唯一": {
    "en": "Anchor label is not unique",
    "zh-Hant": "定位標籤不唯一"
  },
  "版式不符": {
    "en": "Layout mismatch",
    "zh-Hant": "版式不符"
  },
  "必填为空": {
    "en": "Required value is empty",
    "zh-Hant": "必填為空"
  },
  "必填未映射": {
    "en": "Required field is unmapped",
    "zh-Hant": "必填未映射"
  },
  "分段字段": {
    "en": "Split by",
    "zh-Hant": "分段欄位"
  },
  "不分段：每一行明细一份内容块": {
    "en": "No split: one copy of the block per detail row",
    "zh-Hant": "不分段：每一行明細一份內容塊"
  },
  "分段后，分段字段相同的明细行合成一份内容块（按每段第一次出现的顺序）；内容块里的明细表每份只写入该段的行。": {
    "en": "Detail rows with the same split-by value become one copy of the block (in the order each first appears); a detail table inside the block gets only that copy’s rows.",
    "zh-Hant": "分段後，分段欄位相同的明細行合成一份內容塊（按每段第一次出現的順序）；內容塊裡的明細表每份只寫入該段的行。"
  },
  "内容块里有明细表「{v1}」：需要选择分段字段，每一份才会只写入自己那一段的明细行。": {
    "en": "The block contains the detail table “{v1}”: choose a split-by field so each copy gets only its own detail rows.",
    "zh-Hant": "內容塊裡有明細表「{v1}」：需要選擇分段欄位，每一份才會只寫入自己那一段的明細行。"
  },
  "内容块里有明细表：请先选择分段字段，每一份才会只写入自己那一段的明细行": {
    "en": "The block contains a detail table: choose a split-by field first so each copy gets only its own detail rows.",
    "zh-Hant": "內容塊裡有明細表：請先選擇分段欄位，每一份才會只寫入自己那一段的明細行"
  },
  "分段字段为空的明细行": {
    "en": "Detail rows with an empty split-by field",
    "zh-Hant": "分段欄位為空的明細行"
  },
  "报错，不生成这一份（默认）": {
    "en": "Report an error and do not generate this document (default)",
    "zh-Hant": "報錯，不生成這一份（預設）"
  },
  "跳过这些行并提示": {
    "en": "Skip these rows and say so",
    "zh-Hant": "略過這些行並提示"
  },
  "段内取值": {
    "en": "Value per copy",
    "zh-Hant": "段內取值"
  },
  "「{v1}」的段内取值": {
    "en": "Value per copy for “{v1}”",
    "zh-Hant": "「{v1}」的段內取值"
  },
  "必须一致（默认）": {
    "en": "Must be the same (default)",
    "zh-Hant": "必須一致（預設）"
  },
  "取段内第一条": {
    "en": "First in the copy",
    "zh-Hant": "取段內第一筆"
  },
  "取段内最后一条": {
    "en": "Last in the copy",
    "zh-Hant": "取段內最後一筆"
  },
  "此表在内容块「{v1}」内：每一份内容块只写入它那一段的明细行（由内容块的分段字段决定）。": {
    "en": "This table is inside the block “{v1}”: each copy of the block gets only its own detail rows (set by the block’s split-by field).",
    "zh-Hant": "此表在內容塊「{v1}」內：每一份內容塊只寫入它那一段的明細行（由內容塊的分段欄位決定）。"
  },
  "不能共用绑定": {
    "en": "Binding cannot be shared",
    "zh-Hant": "不能共用綁定"
  },
  "计数（该列有值的行数）": {
    "en": "Count (rows where this column has a value)",
    "zh-Hant": "計數（該欄有值的行數）"
  },
  "这个模板的字段在「编辑模板」里添加或删除": {
    "en": "This template's fields are added and removed in “Edit template”",
    "zh-Hant": "這個模板的欄位在「編輯模板」裡新增或刪除"
  },
  "你离开期间，模板被恢复或替换了；你在旧内容上的修改无法合并，这里显示的是已保存的版本。刷新页面即可继续实时协同。": {
    "en": "While you were away the template was restored or replaced; your changes to the old content could not be merged, so this shows the saved version. Refresh the page to continue editing live.",
    "zh-Hant": "你離開期間，模板被恢復或替換了；你在舊內容上的修改無法合併，這裡顯示的是已儲存的版本。重新整理頁面即可繼續即時協同。"
  },
  "两边的修改合在一起无法生成模板：{v1}": {
    "en": " Together, both sets of changes cannot make a template: {v1}",
    "zh-Hant": "兩邊的修改合在一起無法生成模板：{v1}"
  },
  "组内取值冲突": {
    "en": "Conflicting values within group",
    "zh-Hant": "組內取值衝突"
  },
  "内容块未设置分段": {
    "en": "Repeating block not split",
    "zh-Hant": "內容塊未設定分段"
  },
  "明细来源不一致": {
    "en": "Detail sources differ",
    "zh-Hant": "明細來源不一致"
  },
  "不支持分段": {
    "en": "Splitting not supported",
    "zh-Hant": "不支援分段"
  },
  "分段字段为空": {
    "en": "Split-by field empty",
    "zh-Hant": "分段欄位為空"
  },
  "未选择分段字段": {
    "en": "No split-by field chosen",
    "zh-Hant": "未選擇分段欄位"
  },
  "不支持的嵌套": {
    "en": "Unsupported nesting",
    "zh-Hant": "不支援的巢狀"
  },
  "区域尺寸不符": {
    "en": "Region size mismatch",
    "zh-Hant": "區域尺寸不符"
  },
  "目标字段失效": {
    "en": "Invalid target field",
    "zh-Hant": "目標字段失效"
  },
  "多个映射写同一字段": {
    "en": "Multiple mappings target one field",
    "zh-Hant": "多個映射寫同一字段"
  },
  "需要 OCR": {
    "en": "OCR required",
    "zh-Hant": "需要 OCR"
  },
  "OCR 待确认": {
    "en": "OCR confirmation required",
    "zh-Hant": "OCR 待確認"
  },
  "结构不存在": {
    "en": "Structure not found",
    "zh-Hant": "結構不存在"
  },
  "与样本模式不符": {
    "en": "Sample mode mismatch",
    "zh-Hant": "與樣本模式不符"
  },
  "值错误": {
    "en": "Invalid value",
    "zh-Hant": "值錯誤"
  },
  "无明细": {
    "en": "No detail rows",
    "zh-Hant": "無明細"
  },
  "类型不匹配": {
    "en": "Type mismatch",
    "zh-Hant": "類型不匹配"
  },
  "缺少组合规则": {
    "en": "Missing combine rule",
    "zh-Hant": "缺少組合規則"
  },
  "去除首尾空格": {
    "en": "Trim surrounding spaces",
    "zh-Hant": "去除首尾空格"
  },
  "合并连续空白": {
    "en": "Collapse whitespace",
    "zh-Hant": "合併連續空白"
  },
  "日期显示格式": {
    "en": "Date display format",
    "zh-Hant": "日期顯示格式"
  },
  "数字小数位": {
    "en": "Decimal places",
    "zh-Hant": "數字小數位"
  },
  "千位分隔符": {
    "en": "Thousands separator",
    "zh-Hant": "千位分隔符"
  },
  "添加前缀": {
    "en": "Add prefix",
    "zh-Hant": "添加前綴"
  },
  "添加后缀": {
    "en": "Add suffix",
    "zh-Hant": "添加後綴"
  },
  "为空时使用默认值": {
    "en": "Default for empty values",
    "zh-Hant": "為空時使用默認值"
  },
  "值对照替换": {
    "en": "Value replacement table",
    "zh-Hant": "值對照替換"
  },
  "左侧补齐位数": {
    "en": "Pad on the left",
    "zh-Hant": "左側補齊位數"
  },
  "转大写": {
    "en": "Uppercase",
    "zh-Hant": "轉大寫"
  },
  "转小写": {
    "en": "Lowercase",
    "zh-Hant": "轉小寫"
  },
  " 位": {
    "en": " decimal places",
    "zh-Hant": " 位"
  },
  "千位分隔": {
    "en": "Thousands separator",
    "zh-Hant": "千位分隔"
  },
  "宽度 ": {
    "en": "Width ",
    "zh-Hant": "寬度 "
  },
  "补 ": {
    "en": "Pad with ",
    "zh-Hant": "補 "
  },
  "上移": {
    "en": "Move up",
    "zh-Hant": "上移"
  },
  "上移第 {v1} 条转换规则": {
    "en": "Move transformation {v1} up",
    "zh-Hant": "上移第 {v1} 條轉換規則"
  },
  "移除第 {v1} 条转换规则": {
    "en": "Remove transformation {v1}",
    "zh-Hant": "移除第 {v1} 條轉換規則"
  },
  "添加转换规则": {
    "en": "Add transformation",
    "zh-Hant": "添加轉換規則"
  },
  "＋ 添加转换规则…": {
    "en": "＋ Add transformation…",
    "zh-Hant": "＋ 添加轉換規則…"
  },
  "每行一条：原值=新值\n例如 TRUE=是": {
    "en": "One per line: old=new\nExample: TRUE=Yes",
    "zh-Hant": "每行一條：原值=新值\n例如 TRUE=是"
  },
  "值对照表，每行一条：原值=新值": {
    "en": "Value map, one per line: old=new",
    "zh-Hant": "值對照表，每行一條：原值=新值"
  },
  "来源区域无效，请重新选择单元格区域": {
    "en": "Invalid source region. Select the cell range again.",
    "zh-Hant": "來源區域無效，請重新選擇單元格區域"
  },
  "重复明细字段需要在左侧选择「明细表」：包含表头的多行区域，或文档中的整张表格": {
    "en": "A repeating field needs a detail table selected on the left: multiple rows including headers, or an entire document table.",
    "zh-Hant": "重復明細字段需要在左側選擇「明細表」：包含表頭的多行區域，或文檔中的整張表格"
  },
  "没有可用的来源": {
    "en": "No usable source",
    "zh-Hant": "沒有可用的來源"
  },
  "至少需要为一列设置来源列": {
    "en": "Set a source column for at least one column",
    "zh-Hant": "至少需要為一列設置來源列"
  },
  "编辑映射 #{v1}": {
    "en": "Edit mapping #{v1}",
    "zh-Hant": "編輯映射 #{v1}"
  },
  "建立映射": {
    "en": "Create mapping",
    "zh-Hant": "建立映射"
  },
  "保存中…": {
    "en": "Saving…",
    "zh-Hant": "保存中…"
  },
  "保存映射": {
    "en": "Save mapping",
    "zh-Hant": "保存映射"
  },
  "来源 ": {
    "en": "Source ",
    "zh-Hant": "來源 "
  },
  "如需更换来源，请删除后重新建立，或在「校验」中为单个文件重新定位。": {
    "en": "To change the source, recreate the mapping or relocate it for a specific file in Validation.",
    "zh-Hant": "如需更換來源，請刪除後重新建立，或在「校驗」中為單個文件重新定位。"
  },
  "目标是重复明细行：请在左侧选择包含表头的多行区域（例如表头行到最后一行），或点击文档表格左上角「▦」选择整张表。": {
    "en": "For a repeating detail row, select a range including its header on the left, or click “▦” at the table corner to select the whole table.",
    "zh-Hant": "目標是重復明細行：請在左側選擇包含表頭的多行區域（例如表頭行到最後一行），或點擊文檔表格左上角「▦」選擇整張表。"
  },
  "目标是固定区域：请在左侧框选同样大小的单元格区域。": {
    "en": "For a fixed region, select a cell range of the same size on the left.",
    "zh-Hant": "目標是固定區域：請在左側框選同樣大小的單元格區域。"
  },
  "当前选择不能作为单值来源；请选择单个单元格、文字片段或 PDF 区域。": {
    "en": "This selection cannot supply a single value. Select a cell, text span or PDF region.",
    "zh-Hant": "當前選擇不能作為單值來源；請選擇單個單元格、文字片段或 PDF 區域。"
  },
  "✔ 随样本变化：每份输出读取当前样本对应的值": {
    "en": "✔ Varies by sample: each output reads its sample's value",
    "zh-Hant": "✔ 隨樣本變化：每份輸出讀取當前樣本對應的值"
  },
  "● 固定值：同一文件中所有样本使用同一个值": {
    "en": "● Fixed: all samples within the same file use this value",
    "zh-Hant": "● 固定值：同一文件中所有樣本使用同一個值"
  },
  "◆ 随文件变化：每个来源文件在相同位置取值": {
    "en": "◆ Varies by file: reads the same location in each source file",
    "zh-Hant": "◆ 隨文件變化：每個來源文件在相同位置取值"
  },
  "示例值：": {
    "en": "Sample value: ",
    "zh-Hant": "示例值："
  },
  "（空）": {
    "en": "(empty)",
    "zh-Hant": "（空）"
  },
  "将替换字段「": {
    "en": "Replace the existing mapping for field “",
    "zh-Hant": "將替換字段「"
  },
  "」的原映射 #": {
    "en": "”, mapping #",
    "zh-Hant": "」的原映射 #"
  },
  "字段「": {
    "en": "Field “",
    "zh-Hant": "字段「"
  },
  "」已有映射 #": {
    "en": "” already has mapping #",
    "zh-Hant": "」已有映射 #"
  },
  " 作为第 ": {
    "en": " Merge as source ",
    "zh-Hant": " 作為第 "
  },
  " 个来源合并写入：": {
    "en": ": ",
    "zh-Hant": " 個來源合併寫入："
  },
  "拼接": {
    "en": "Concatenate",
    "zh-Hant": "拼接"
  },
  "换行拼接": {
    "en": "Join with newlines",
    "zh-Hant": "換行拼接"
  },
  "求和（数值）": {
    "en": "Sum (numeric)",
    "zh-Hant": "求和（數值）"
  },
  "分隔符 ": {
    "en": "Separator ",
    "zh-Hant": "分隔符 "
  },
  " 替换原映射 #": {
    "en": " Replace mapping #",
    "zh-Hant": " 替換原映射 #"
  },
  "目标字段": {
    "en": "Target field",
    "zh-Hant": "目標字段"
  },
  " 行 × ": {
    "en": " rows × ",
    "zh-Hant": " 行 × "
  },
  " 列": {
    "en": " columns",
    "zh-Hant": " 列"
  },
  "必填": {
    "en": "Required",
    "zh-Hant": "必填"
  },
  "目标列": {
    "en": "Target column",
    "zh-Hant": "目標列"
  },
  "来源列": {
    "en": "Source column",
    "zh-Hant": "來源列"
  },
  "（保留原常量）": {
    "en": "(keep original constant)",
    "zh-Hant": "（保留原常量）"
  },
  "（留空）": {
    "en": "(leave empty)",
    "zh-Hant": "（留空）"
  },
  "（重名）": {
    "en": "(duplicate name)",
    "zh-Hant": "（重名）"
  },
  "零条明细时的处理方式、表头跨页重复等在「字段」面板中设置。": {
    "en": "Configure empty-detail behavior and repeating page headers in the Fields panel.",
    "zh-Hant": "零條明細時的處理方式、表頭跨頁重復等在「字段」面板中設置。"
  },
  "值转换": {
    "en": "Transform values",
    "zh-Hant": "值轉換"
  },
  "组内取值规则（同一组有多行时）": {
    "en": "Values within a group (multiple rows)",
    "zh-Hant": "組內取值規則（同一組有多行時）"
  },
  "必须一致，不一致时报冲突（默认）": {
    "en": "Require equal values; report conflicts (default)",
    "zh-Hant": "必須一致，不一致時報衝突（默認）"
  },
  "取组内第一条（按排序规则）": {
    "en": "First row (in sort order)",
    "zh-Hant": "取組內第一條（按排序規則）"
  },
  "取组内最后一条": {
    "en": "Last row",
    "zh-Hant": "取組內最後一條"
  },
  "全部拼接": {
    "en": "Join all",
    "zh-Hant": "全部拼接"
  },
  "去重后拼接": {
    "en": "Join unique values",
    "zh-Hant": "去重後拼接"
  },
  "求和": {
    "en": "Sum",
    "zh-Hant": "求和"
  },
  "计数（行数）": {
    "en": "Count (rows)",
    "zh-Hant": "計數（行數）"
  },
  "分隔符": {
    "en": "Separator",
    "zh-Hant": "分隔符"
  },
  "写入方式": {
    "en": "Write mode",
    "zh-Hant": "寫入方式"
  },
  "按值类型写入（数字仍是数字，日期仍是日期；显示格式沿用目标单元格）": {
    "en": "Preserve value types (numbers and dates); retain the target cell's display format",
    "zh-Hant": "按值類型寫入（數字仍是數字，日期仍是日期；顯示格式沿用目標單元格）"
  },
  "写入来源显示文本（如 000123、¥1,000.00）": {
    "en": "Use source display text (e.g. 000123, ¥1,000.00)",
    "zh-Hant": "寫入來源顯示文本（如 000123、¥1,000.00）"
  },
  "样式": {
    "en": "Style",
    "zh-Hant": "樣式"
  },
  "只传递数据，沿用目标模板样式（默认）": {
    "en": "Transfer data and retain template styling (default)",
    "zh-Hant": "只傳遞數據，沿用目標模板樣式（默認）"
  },
  "复制来源样式（加粗/斜体/下划线/颜色）": {
    "en": "Copy source styling (bold/italic/underline/color)",
    "zh-Hant": "複製來源樣式（加粗/斜體/下划線/顏色）"
  },
  " — 仅 DOCX→DOCX 支持": {
    "en": " — DOCX→DOCX only",
    "zh-Hant": " — 僅 DOCX→DOCX 支持"
  },
  "提交任务所用的凭据已被吊销或已过期": {
    "en": "The credentials used to submit this job were revoked or expired",
    "zh-Hant": "提交任務所用的憑據已被吊銷或已過期"
  },
  "提交者已不再拥有写入权限": {
    "en": "The submitter no longer has write access",
    "zh-Hant": "提交者已不再擁有寫入權限"
  },
  "提交者在该工作空间的角色已不允许生成": {
    "en": "The submitter's workspace role no longer allows generation",
    "zh-Hant": "提交者在該工作空間的角色已不允許生成"
  },
  "提交者已不是该工作空间的成员": {
    "en": "The submitter is no longer a workspace member",
    "zh-Hant": "提交者已不是該工作空間的成員"
  },
  "提交者的账户已被停用或删除": {
    "en": "The submitter's account was disabled or deleted",
    "zh-Hant": "提交者的賬戶已被停用或刪除"
  },
  "提交者对 Synwevo Docs 的使用资格已被收回": {
    "en": "The submitter's Synwevo Docs entitlement was revoked",
    "zh-Hant": "提交者對 Synwevo Docs 的使用資格已被收回"
  },
  "提交者没有 Synwevo Docs 的使用资格": {
    "en": "The submitter has no Synwevo Docs entitlement",
    "zh-Hant": "提交者沒有 Synwevo Docs 的使用資格"
  },
  "提交任务所用的授权已被撤销": {
    "en": "The authorization used to submit the job was revoked",
    "zh-Hant": "提交任務所用的授權已被撤銷"
  },
  "提交任务所用的授权不是为 Synwevo Docs 签发的": {
    "en": "The authorization was not issued for Synwevo Docs",
    "zh-Hant": "提交任務所用的授權不是為 Synwevo Docs 簽發的"
  },
  "提交者的凭据不属于该工作空间": {
    "en": "The submitter's credentials belong to another workspace",
    "zh-Hant": "提交者的憑據不屬於該工作空間"
  },
  "工作空间当前不可用": {
    "en": "The workspace is unavailable",
    "zh-Hant": "工作空間當前不可用"
  },
  "工作空间已被冻结": {
    "en": "The workspace is frozen",
    "zh-Hant": "工作空間已被凍結"
  },
  "Synwevo Docs 已被平台停用": {
    "en": "Synwevo Docs is disabled on this platform",
    "zh-Hant": "Synwevo Docs 已被平台停用"
  },
  "该工作空间已不再启用 Synwevo Docs": {
    "en": "Synwevo Docs is no longer enabled for this workspace",
    "zh-Hant": "該工作空間已不再啓用 Synwevo Docs"
  },
  "无法确认提交者的身份": {
    "en": "The submitter's identity could not be verified",
    "zh-Hant": "無法確認提交者的身份"
  },
  "任务已停止：数据从备份恢复时，这个任务还没有完成；恢复后不会自动继续，未完成的部分可以用「重试」重新生成。": {
    "en": "Job stopped: it was unfinished when data was restored from backup. It will not resume automatically. Use Retry to generate unfinished outputs.",
    "zh-Hant": "任務已停止：數據從備份恢復時，這個任務還沒有完成；恢復後不會自動繼續，未完成的部分可以用「重試」重新生成。"
  },
  "提交者已失去权限": {
    "en": "The submitter lost access",
    "zh-Hant": "提交者已失去權限"
  },
  "任务已停止：{v1}。已生成的文件保留，不会再生成新的文件。": {
    "en": "Job stopped: {v1}. Existing outputs are retained; no new files will be generated.",
    "zh-Hant": "任務已停止：{v1}。已生成的文件保留，不會再生成新的文件。"
  },
  "任务已停止：{v1}": {
    "en": "Job stopped: {v1}",
    "zh-Hant": "任務已停止：{v1}"
  },
  "无法重新打开": {
    "en": "Could not reopen",
    "zh-Hant": "無法重新打開"
  },
  "对应程序": {
    "en": "the corresponding application",
    "zh-Hant": "對應程序"
  },
  "{v1} 个值未找到": {
    "en": "{v1} values not found",
    "zh-Hant": "{v1} 個值未找到"
  },
  "已用 {v1} 重新打开，核对 {v2} 个值，其中 {v3} 个未在文件中找到": {
    "en": "Reopened with {v1}; checked {v2} values, of which {v3} were not found",
    "zh-Hant": "已用 {v1} 重新打開，核對 {v2} 個值，其中 {v3} 個未在文件中找到"
  },
  "✓ 已校验": {
    "en": "✓ Verified",
    "zh-Hant": "✓ 已校驗"
  },
  "已用 {v1} 重新打开并核对 {v2} 个值": {
    "en": "Reopened with {v1} and checked {v2} values",
    "zh-Hant": "已用 {v1} 重新打開並核對 {v2} 個值"
  },
  "已重新打开": {
    "en": "Reopened",
    "zh-Hant": "已重新打開"
  },
  "已用 {v1} 重新打开；此格式没有逐值核对": {
    "en": "Reopened with {v1}; this format has no value-by-value verification",
    "zh-Hant": "已用 {v1} 重新打開；此格式沒有逐值核對"
  },
  "真实样本预览": {
    "en": "Preview actual sample",
    "zh-Hant": "真實樣本預覽"
  },
  "‹ 上一条": {
    "en": "‹ Previous",
    "zh-Hant": "‹ 上一條"
  },
  "下一条 ›": {
    "en": "Next ›",
    "zh-Hant": "下一條 ›"
  },
  "按样本标识查找": {
    "en": "Find by sample identifier",
    "zh-Hant": "按樣本標識查找"
  },
  "✓ 真实分页": {
    "en": "✓ Actual pagination",
    "zh-Hant": "✓ 真實分頁"
  },
  "真实分页": {
    "en": "Actual pagination",
    "zh-Hant": "真實分頁"
  },
  "预览_": {
    "en": "Preview_",
    "zh-Hant": "預覽_"
  },
  "下载此预览": {
    "en": "Download this preview",
    "zh-Hant": "下載此預覽"
  },
  "缩小预览": {
    "en": "Zoom out",
    "zh-Hant": "縮小預覽"
  },
  "放大预览": {
    "en": "Zoom in",
    "zh-Hant": "放大預覽"
  },
  "返回模板": {
    "en": "Back to template",
    "zh-Hant": "返回模板"
  },
  "来源：": {
    "en": "Source: ",
    "zh-Hant": "來源："
  },
  "跨文件合并：": {
    "en": "Merged across files: ",
    "zh-Hant": "跨文件合併："
  },
  "✓ 已校验 {v1} 个值": {
    "en": "✓ Verified {v1} values",
    "zh-Hant": "✓ 已校驗 {v1} 個值"
  },
  "已启用": {
    "en": "Enabled",
    "zh-Hant": "已啟用"
  },
  "未启用": {
    "en": "Not enabled",
    "zh-Hant": "未啟用"
  },
  "文档读取器": {
    "en": "the document reader",
    "zh-Hant": "文件讀取器"
  },
  "表格读取器": {
    "en": "the spreadsheet reader",
    "zh-Hant": "試算表讀取器"
  },
  "PDF 引擎": {
    "en": "the PDF engine",
    "zh-Hant": "PDF 引擎"
  },
  "演示文稿读取器": {
    "en": "the presentation reader",
    "zh-Hant": "簡報讀取器"
  },
  "CSV 读取器": {
    "en": "the CSV reader",
    "zh-Hant": "CSV 讀取器"
  },
  "图像读取器": {
    "en": "the image reader",
    "zh-Hant": "圖像讀取器"
  },
  "此项目由 {who} 的个人项目「{name}」经审批复制而来": {
    "en": "This project was copied, with approval, from {who}’s personal project “{name}”",
    "zh-Hant": "此專案由 {who} 的個人專案「{name}」經審批複製而來"
  },
  "{who} 自行批准": {
    "en": "Self-approved by {who}",
    "zh-Hant": "{who} 自行批准"
  },
  "由 {who} 批准": {
    "en": "Approved by {who}",
    "zh-Hant": "由 {who} 批准"
  },
  "未包含来源文件，请上传本空间的数据后再生成": {
    "en": "Source files were not included; upload this workspace’s data before generating",
    "zh-Hant": "未包含來源檔案，請上傳本空間的資料後再產生"
  },
  "查看审批记录": {
    "en": "View the approval",
    "zh-Hant": "查看審批記錄"
  },
  "整个项目": {
    "en": "The whole project",
    "zh-Hant": "整個專案"
  },
  "当前映射": {
    "en": "The selected mapping",
    "zh-Hant": "目前的對應"
  },
  "文件「{v1}」": {
    "en": "File “{v1}”",
    "zh-Hant": "檔案「{v1}」"
  },
  "评论…": {
    "en": "Comments…",
    "zh-Hant": "評論…"
  },
  "评论": {
    "en": "Comments",
    "zh-Hant": "評論"
  },
  "显示已解决": {
    "en": "Show resolved",
    "zh-Hant": "顯示已解決"
  },
  "还没有评论。选中一个字段、映射或文件后评论，可以把问题定位到它。": {
    "en": "No comments yet. Select a field, a mapping or a file before commenting to pin the question to it.",
    "zh-Hant": "還沒有評論。選取一個欄位、對應或檔案後評論，可以把問題定位到它。"
  },
  "回复 {who}": {
    "en": "Replying to {who}",
    "zh-Hant": "回覆 {who}"
  },
  "正在编辑你的评论": {
    "en": "Editing your comment",
    "zh-Hant": "正在編輯你的評論"
  },
  "关于": {
    "en": "About",
    "zh-Hant": "關於"
  },
  "写评论… 输入 @ 提及成员": {
    "en": "Write a comment… type @ to mention a member",
    "zh-Hant": "寫評論… 輸入 @ 提及成員"
  },
  "保存修改": {
    "en": "Save changes",
    "zh-Hant": "儲存修改"
  },
  "发布评论": {
    "en": "Post comment",
    "zh-Hant": "發佈評論"
  },
  "评论已修改": {
    "en": "Comment updated",
    "zh-Hant": "評論已修改"
  },
  "评论已发布，已通知被提及的成员": {
    "en": "Comment posted; mentioned members were notified",
    "zh-Hant": "評論已發佈，已通知被提及的成員"
  },
  "评论已发布": {
    "en": "Comment posted",
    "zh-Hant": "評論已發佈"
  },
  "任务": {
    "en": "Job",
    "zh-Hant": "任務"
  },
  "已失效": {
    "en": "stale",
    "zh-Hant": "已失效"
  },
  "定位到该对象": {
    "en": "Go to it",
    "zh-Hant": "定位到該物件"
  },
  "已更新": {
    "en": "updated since",
    "zh-Hant": "已更新"
  },
  "该字段已删除": {
    "en": "That field was deleted",
    "zh-Hant": "該欄位已刪除"
  },
  "模板已更换": {
    "en": "The template was replaced",
    "zh-Hant": "模板已更換"
  },
  "该映射已删除": {
    "en": "That mapping was deleted",
    "zh-Hant": "該對應已刪除"
  },
  "该文件已移除": {
    "en": "That file was removed",
    "zh-Hant": "該檔案已移除"
  },
  "锚点已失效": {
    "en": "The anchor is stale",
    "zh-Hant": "錨點已失效"
  },
  "已编辑": {
    "en": "edited",
    "zh-Hant": "已編輯"
  },
  "回复": {
    "en": "Reply",
    "zh-Hant": "回覆"
  },
  "删除评论": {
    "en": "Delete comment",
    "zh-Hant": "刪除評論"
  },
  "删除后无法恢复。": {
    "en": "This cannot be undone.",
    "zh-Hant": "刪除後無法復原。"
  },
  "重新打开": {
    "en": "Reopen",
    "zh-Hant": "重新開啟"
  },
  "标记解决": {
    "en": "Resolve",
    "zh-Hant": "標記解決"
  },
  "由 {who} 标记解决": {
    "en": "Resolved by {who}",
    "zh-Hant": "由 {who} 標記解決"
  },
  "模板 ": {
    "en": "Template ",
    "zh-Hant": "模板 "
  },
  " 页 → 本份 ": {
    "en": " pages → this output ",
    "zh-Hant": " 頁 → 本份 "
  },
  " 页": {
    "en": " pages",
    "zh-Hant": " 頁"
  },
  "（分页增加）": {
    "en": "(extra pages)",
    "zh-Hant": "（分頁增加）"
  },
  "已按「保持模板页数」处理：": {
    "en": "Applied “Keep template page count”: ",
    "zh-Hant": "已按「保持模板頁數」處理："
  },
  "，字段缩小到 {v1}%": {
    "en": "; fields reduced to {v1}%",
    "zh-Hant": "，字段縮小到 {v1}%"
  },
  "重试当前样本": {
    "en": "Retry this sample",
    "zh-Hant": "重試當前樣本"
  },
  "该样本存在错误，无法生成预览": {
    "en": "This sample has errors and cannot be previewed",
    "zh-Hant": "該樣本存在錯誤，無法生成預覽"
  },
  "请根据上方提示修正映射或数据": {
    "en": "Fix the mapping or data using the messages above",
    "zh-Hant": "請根據上方提示修正映射或數據"
  },
  "正在生成预览…": {
    "en": "Generating preview…",
    "zh-Hant": "正在生成預覽…"
  },
  "保存为回归用例": {
    "en": "Save as regression case",
    "zh-Hant": "保存為回歸用例"
  },
  "查看回归用例": {
    "en": "View regression cases",
    "zh-Hant": "查看回歸用例"
  },
  "完成": {
    "en": "Done",
    "zh-Hant": "完成"
  },
  "正在生成期望结果…": {
    "en": "Generating expected results…",
    "zh-Hant": "正在生成期望結果…"
  },
  "保存用例": {
    "en": "Save case",
    "zh-Hant": "保存用例"
  },
  "已保存「": {
    "en": "Saved “",
    "zh-Hant": "已保存「"
  },
  " 个样本的期望结果（共 ": {
    "en": " sample expectations (of ",
    "zh-Hant": " 個樣本的期望結果（共 "
  },
  " 个样本）。": {
    "en": " samples).",
    "zh-Hant": " 個樣本）。"
  },
  "请只保存测试数据或已脱敏的文件": {
    "en": "Save only test data or anonymized files",
    "zh-Hant": "請只保存測試數據或已脫敏的文件"
  },
  "（可先用「生成脱敏副本」）。": {
    "en": " (use “Create anonymized copies” first if needed).",
    "zh-Hant": "（可先用「生成脫敏副本」）。"
  },
  "用例名称": {
    "en": "Case name",
    "zh-Hant": "用例名稱"
  },
  "说明（可选）": {
    "en": "Description (optional)",
    "zh-Hant": "說明（可選）"
  },
  "例如：2026 年第三季度真实订单（已脱敏），含 3 个跨页订单": {
    "en": "Example: anonymized Q3 2026 orders, including 3 orders spanning pages",
    "zh-Hant": "例如：2026 年第三季度真實訂單（已脫敏），含 3 個跨頁訂單"
  },
  "手机号": {
    "en": "Mobile phone",
    "zh-Hant": "手機號"
  },
  "身份证号（保留出生日期）": {
    "en": "Identity number (retain birth date)",
    "zh-Hant": "身份證號（保留出生日期）"
  },
  "银行卡号": {
    "en": "Bank card number",
    "zh-Hant": "銀行卡號"
  },
  "电子邮箱": {
    "en": "Email address",
    "zh-Hant": "電子郵箱"
  },
  "固定电话": {
    "en": "Landline",
    "zh-Hant": "固定電話"
  },
  "不变": {
    "en": "Unchanged",
    "zh-Hant": "不變"
  },
  "替换为代号（同值同代号）": {
    "en": "Replace with aliases (same value, same alias)",
    "zh-Hant": "替換為代號（同值同代號）"
  },
  "保留首尾，中间打码": {
    "en": "Mask the middle, retain both ends",
    "zh-Hant": "保留首尾，中間打碼"
  },
  "数字随机替换（保持长度）": {
    "en": "Randomize digits (retain length)",
    "zh-Hant": "數字隨機替換（保持長度）"
  },
  "数值按同一比例缩放": {
    "en": "Scale numbers by a common factor",
    "zh-Hant": "數值按同一比例縮放"
  },
  "日期整体平移": {
    "en": "Shift all dates",
    "zh-Hant": "日期整體平移"
  },
  "清空": {
    "en": "Clear",
    "zh-Hant": "清空"
  },
  "生成脱敏副本": {
    "en": "Create anonymized copies",
    "zh-Hant": "生成脫敏副本"
  },
  "正在处理…": {
    "en": "Processing…",
    "zh-Hant": "正在處理…"
  },
  "生成 {v1} 个脱敏副本": {
    "en": "Create {v1} anonymized copies",
    "zh-Hant": "生成 {v1} 個脫敏副本"
  },
  "已生成 ": {
    "en": "Created ",
    "zh-Hant": "已生成 "
  },
  " 个脱敏副本": {
    "en": " anonymized copies",
    "zh-Hant": " 個脫敏副本"
  },
  "，并已加入来源文件列表": {
    "en": " and added them to source files",
    "zh-Hant": "，並已加入來源文件列表"
  },
  "。同一个原值在所有文件中替换为同一个代号（本次共 ": {
    "en": ". Identical values receive the same alias across files (total: ",
    "zh-Hant": "。同一個原值在所有文件中替換為同一個代號（本次共 "
  },
  " 个）。": {
    "en": ").",
    "zh-Hant": " 個）。"
  },
  "原文件": {
    "en": "Original file",
    "zh-Hant": "原文件"
  },
  "脱敏副本": {
    "en": "Anonymized copy",
    "zh-Hant": "脫敏副本"
  },
  "下载": {
    "en": "Download",
    "zh-Hant": "下載"
  },
  "」未处理：": {
    "en": "” was not processed: ",
    "zh-Hant": "」未處理："
  },
  "替换统计：": {
    "en": "Replacement statistics: ",
    "zh-Hant": "替換統計："
  },
  "替换对照表不会保存；请打开副本确认没有遗漏后再用于回归用例或发送给他人。": {
    "en": "Replacement lookup tables are not saved. Check the copies for omissions before using them in regression cases or sharing them.",
    "zh-Hant": "替換對照表不會保存；請打開副本確認沒有遺漏後再用於回歸用例或發送給他人。"
  },
  "要处理的文件": {
    "en": "Files to process",
    "zh-Hant": "要處理的文件"
  },
  "PDF / 图片 / 幻灯片中的文字无法在不破坏版式的前提下可靠替换": {
    "en": "Text in PDFs, images and slides cannot be reliably replaced while preserving layout",
    "zh-Hant": "PDF / 圖片 / 幻燈片中的文字無法在不破壞版式的前提下可靠替換"
  },
  "不支持": {
    "en": "Unsupported",
    "zh-Hant": "不支持"
  },
  "自动识别并替换": {
    "en": "Automatically detect and replace",
    "zh-Hant": "自動識別並替換"
  },
  "另外要替换的词（人名、单位名等，每行一个或用逗号分隔）": {
    "en": "Additional terms (names, organizations, etc.; one per line or comma-separated)",
    "zh-Hant": "另外要替換的詞（人名、單位名等，每行一個或用逗號分隔）"
  },
  "张三\n某某科技有限公司": {
    "en": "Jane Smith\nExample Technology Ltd.",
    "zh-Hant": "張三\n某某科技有限公司"
  },
  " 把脱敏副本加入当前项目的来源文件": {
    "en": " Add anonymized copies to this project's sources",
    "zh-Hant": " 把脫敏副本加入當前項目的來源文件"
  },
  "表格列的处理方式 ": {
    "en": "Spreadsheet column treatment ",
    "zh-Hant": "表格列的處理方式 "
  },
  "（按「": {
    "en": " (headers from “",
    "zh-Hant": "（按「"
  },
  "」的表头）": {
    "en": "”)",
    "zh-Hant": "」的表頭）"
  },
  "选择了表格文件（XLSX / CSV）时，可以逐列设置处理方式；其余单元格按左侧规则替换。": {
    "en": "For spreadsheet files (XLSX / CSV), set treatment per column. Other cells follow the rules on the left.",
    "zh-Hant": "選擇了表格文件（XLSX / CSV）時，可以逐列設置處理方式；其餘單元格按左側規則替換。"
  },
  "表格中每条有效数据行 = 一份输出": {
    "en": "Each valid spreadsheet row = one output",
    "zh-Hant": "表格中每條有效數據行 = 一份輸出"
  },
  "例：客户表 100 行 → 生成 100 份合同": {
    "en": "Example: 100 customer rows → 100 contracts",
    "zh-Hant": "例：客戶表 100 行 → 生成 100 份合同"
  },
  "每个来源文件 = 一份输出（文件内多处数据、多个明细表共同填入）": {
    "en": "Each source file = one output, combining values and detail tables within that file",
    "zh-Hant": "每個來源文件 = 一份輸出（文件內多處數據、多個明細表共同填入）"
  },
  "例：20 份检测表 → 生成 20 份报告": {
    "en": "Example: 20 inspection forms → 20 reports",
    "zh-Hant": "例：20 份檢測表 → 生成 20 份報告"
  },
  "按选定字段（如订单号）分组，每组 = 一份输出，组内多行作为明细": {
    "en": "Group by selected fields (e.g. order number); each group = one output with its rows as details",
    "zh-Hant": "按選定字段（如訂單號）分組，每組 = 一份輸出，組內多行作為明細"
  },
  "例：100 条明细属于 15 个订单 → 生成 15 份订单": {
    "en": "Example: 100 detail rows across 15 orders → 15 order documents",
    "zh-Hant": "例：100 條明細屬於 15 個訂單 → 生成 15 份訂單"
  },
  "一个文件里有多张表单：按每块开头的标记文字切开，每块 = 一份输出": {
    "en": "Split forms within a file at a start marker; each block = one output",
    "zh-Hant": "一個文件里有多張表單：按每塊開頭的標記文字切開，每塊 = 一份輸出"
  },
  "例：一个 PDF 里 30 张登记表 → 生成 30 份": {
    "en": "Example: 30 forms in one PDF → 30 outputs",
    "zh-Hant": "例：一個 PDF 里 30 張登記表 → 生成 30 份"
  },
  "请填写每个表单块开头的标记文字": {
    "en": "Enter the text marking the start of each form block",
    "zh-Hant": "請填寫每個表單塊開頭的標記文字"
  },
  "请选择分组字段": {
    "en": "Select grouping fields",
    "zh-Hant": "請選擇分組字段"
  },
  "定义样本：每份输出对应什么数据？": {
    "en": "Define samples: what data makes one output?",
    "zh-Hant": "定義樣本：每份輸出對應什麼數據？"
  },
  "将识别 ": {
    "en": "Will detect ",
    "zh-Hant": "將識別 "
  },
  " 条样本 → 默认生成 ": {
    "en": " samples → by default, generate ",
    "zh-Hant": " 條樣本 → 默認生成 "
  },
  " 份文档": {
    "en": " documents",
    "zh-Hant": " 份文檔"
  },
  "确认样本规则": {
    "en": "Confirm sample rules",
    "zh-Hant": "確認樣本規則"
  },
  "正在识别当前规则下的样本…": {
    "en": "Detecting samples with these rules…",
    "zh-Hant": "正在識別當前規則下的樣本…"
  },
  "」需要表格类来源（XLSX/CSV/XLS/ODS 或含表格的 HTML）。当前没有可用的表格文件。": {
    "en": "” requires spreadsheet sources (XLSX/CSV/XLS/ODS or HTML tables). No suitable source is ready.",
    "zh-Hant": "」需要表格類來源（XLSX/CSV/XLS/ODS 或含表格的 HTML）。當前沒有可用的表格文件。"
  },
  "如何切分表单块": {
    "en": "How to split form blocks",
    "zh-Hant": "如何切分表單塊"
  },
  "每块开头的标记文字": {
    "en": "Start marker text",
    "zh-Hant": "每塊開頭的標記文字"
  },
  "例如：人员登记表、姓名：、检测报告编号": {
    "en": "Example: Registration form, Name:, Report number",
    "zh-Hant": "例如：人員登記表、姓名：、檢測報告編號"
  },
  "使用左侧当前选中的文字": {
    "en": "Use the text selected on the left",
    "zh-Hant": "使用左側當前選中的文字"
  },
  "用选中文字「": {
    "en": "Use selected text “",
    "zh-Hant": "用選中文字「"
  },
  "匹配方式": {
    "en": "Matching mode",
    "zh-Hant": "匹配方式"
  },
  "所在行 / 段落包含该文字": {
    "en": "Line / paragraph contains this text",
    "zh-Hant": "所在行 / 段落包含該文字"
  },
  "所在行 / 段落以该文字开头": {
    "en": "Line / paragraph starts with this text",
    "zh-Hant": "所在行 / 段落以該文字開頭"
  },
  "所在行 / 段落正好是该文字": {
    "en": "Line / paragraph exactly matches this text",
    "zh-Hant": "所在行 / 段落正好是該文字"
  },
  "正则表达式": {
    "en": "Regular expression",
    "zh-Hant": "正則表達式"
  },
  "结束标记（可选）": {
    "en": "End marker (optional)",
    "zh-Hant": "結束標記（可選）"
  },
  "留空 = 到下一个开头标记": {
    "en": "Empty = until the next start marker",
    "zh-Hant": "留空 = 到下一個開頭標記"
  },
  "表格文件使用的工作表": {
    "en": "Worksheet for spreadsheet files",
    "zh-Hant": "表格文件使用的工作表"
  },
  "识别结果预览": {
    "en": "Detection preview",
    "zh-Hant": "識別結果預覽"
  },
  "识别表单块": {
    "en": "Detected form blocks",
    "zh-Hant": "識別表單塊"
  },
  "包含表单块的文件": {
    "en": "Files containing form blocks",
    "zh-Hant": "包含表單塊的文件"
  },
  "未找到标记的文件": {
    "en": "Files without matching markers",
    "zh-Hant": "未找到標記的文件"
  },
  "表单块": {
    "en": "Form block",
    "zh-Hant": "表單塊"
  },
  "位置": {
    "en": "Position",
    "zh-Hant": "位置"
  },
  "数据范围 ": {
    "en": "Data range ",
    "zh-Hant": "數據範圍 "
  },
  "（以": {
    "en": " (based on ",
    "zh-Hant": "（以"
  },
  "代表文件": {
    "en": "Representative file",
    "zh-Hant": "代表文件"
  },
  "表格文件": {
    "en": "Spreadsheet file",
    "zh-Hant": "表格文件"
  },
  "」为准）": {
    "en": "”)",
    "zh-Hant": "」為準）"
  },
  " 行）": {
    "en": " rows)",
    "zh-Hant": " 行）"
  },
  "表头行（第几行）": {
    "en": "Header row number",
    "zh-Hant": "表頭行（第幾行）"
  },
  "无表头": {
    "en": "No header",
    "zh-Hant": "無表頭"
  },
  "数据起始行（留空 = 表头下一行）": {
    "en": "First data row (empty = after header)",
    "zh-Hant": "數據起始行（留空 = 表頭下一行）"
  },
  "数据结束行（留空 = 到最后一行）": {
    "en": "Last data row (empty = final row)",
    "zh-Hant": "數據結束行（留空 = 到最後一行）"
  },
  "末尾": {
    "en": "End",
    "zh-Hant": "末尾"
  },
  "表头：": {
    "en": "Headers: ",
    "zh-Hant": "表頭："
  },
  "过滤条件（可选）": {
    "en": "Filters (optional)",
    "zh-Hant": "過濾條件（可選）"
  },
  "选择列": {
    "en": "Select column",
    "zh-Hant": "選擇列"
  },
  "不为空": {
    "en": "Is not empty",
    "zh-Hant": "不為空"
  },
  "为空": {
    "en": "Is empty",
    "zh-Hant": "為空"
  },
  "等于": {
    "en": "Equals",
    "zh-Hant": "等於"
  },
  "不等于": {
    "en": "Does not equal",
    "zh-Hant": "不等於"
  },
  "包含": {
    "en": "Contains",
    "zh-Hant": "包含"
  },
  "不包含": {
    "en": "Does not contain",
    "zh-Hant": "不包含"
  },
  "＋ 添加过滤": {
    "en": "＋ Add filter",
    "zh-Hant": "＋ 添加過濾"
  },
  "样本标识列（用于查找和命名，可选）": {
    "en": "Sample identifier column (for lookup and naming, optional)",
    "zh-Hant": "樣本標識列（用於查找和命名，可選）"
  },
  "（不设置）": {
    "en": "(not set)",
    "zh-Hant": "（不設置）"
  },
  "分组": {
    "en": "Grouping",
    "zh-Hant": "分組"
  },
  "分组字段（可多选，按组合值分组）": {
    "en": "Group fields (multiple fields form a combined key)",
    "zh-Hant": "分組字段（可多選，按組合值分組）"
  },
  "组内排序": {
    "en": "Sort within group",
    "zh-Hant": "組內排序"
  },
  "保持原始顺序": {
    "en": "Keep original order",
    "zh-Hant": "保持原始順序"
  },
  "升序": {
    "en": "Ascending",
    "zh-Hant": "升序"
  },
  "降序": {
    "en": "Descending",
    "zh-Hant": "降序"
  },
  "按数值": {
    "en": "Numeric",
    "zh-Hant": "按數值"
  },
  "多文件时": {
    "en": "Multiple files",
    "zh-Hant": "多文件時"
  },
  "每个文件内部单独分组（默认，不同文件的相同编号不会合并）": {
    "en": "Group within each file (default; equal keys in separate files stay separate)",
    "zh-Hant": "每個文件內部單獨分組（默認，不同文件的相同編號不會合併）"
  },
  "跨文件按编号合并分组（会展示合并结果与冲突）": {
    "en": "Merge groups across files by key (merged results and conflicts are shown)",
    "zh-Hant": "跨文件按編號合併分組（會展示合併結果與衝突）"
  },
  "排除空白行": {
    "en": "Empty rows excluded",
    "zh-Hant": "排除空白行"
  },
  "过滤 / 分组值为空": {
    "en": "Filtered / empty group keys",
    "zh-Hant": "過濾 / 分組值為空"
  },
  "样本": {
    "en": "Samples",
    "zh-Hant": "樣本"
  },
  "合并自：": {
    "en": "Merged from: ",
    "zh-Hant": "合併自："
  },
  "仅显示前 ": {
    "en": "Showing the first ",
    "zh-Hant": "僅顯示前 "
  },
  " 条，完整清单在「样本」面板中查看。": {
    "en": " only. Open the Samples panel for the full list.",
    "zh-Hant": " 條，完整清單在「樣本」面板中查看。"
  },
  "将为 ": {
    "en": "Generate one output for each of ",
    "zh-Hant": "將為 "
  },
  " 个可用来源文件各生成一份输出。文件内不同位置的数据、多个明细区域可以共同填入同一份文档。": {
    "en": " ready source files. Values from different locations and multiple detail regions within a file can populate the same document.",
    "zh-Hant": " 個可用來源文件各生成一份輸出。文件內不同位置的數據、多個明細區域可以共同填入同一份文檔。"
  },
  "展开映射列表": {
    "en": "Expand mapping list",
    "zh-Hant": "展開映射列表"
  },
  "映射 ": {
    "en": "Mappings ",
    "zh-Hant": "映射 "
  },
  " · 字段 ": {
    "en": " · Fields ",
    "zh-Hant": " · 字段 "
  },
  "调整右侧面板宽度": {
    "en": "Resize right panel",
    "zh-Hant": "調整右側面板寬度"
  },
  "{v1} 像素": {
    "en": "{v1} pixels",
    "zh-Hant": "{v1} 像素"
  },
  "拖动或用 ← / → 调整面板宽度，Enter 收起": {
    "en": "Drag or use ← / → to resize; Enter to collapse",
    "zh-Hant": "拖動或用 ← / → 調整面板寬度，Enter 收起"
  },
  "字段 ": {
    "en": "Fields ",
    "zh-Hant": "字段 "
  },
  "校验": {
    "en": "Validation",
    "zh-Hant": "校驗"
  },
  "收起右侧面板": {
    "en": "Collapse right panel",
    "zh-Hant": "收起右側面板"
  },
  "启用": {
    "en": "Enable",
    "zh-Hant": "啓用"
  },
  "禁用": {
    "en": "Disable",
    "zh-Hant": "禁用"
  },
  "删除映射": {
    "en": "Delete mappings",
    "zh-Hant": "刪除映射"
  },
  "删除 {v1} 个映射（#{v2}）？可以用「撤销」恢复。": {
    "en": "Delete {v1} mappings (#{v2})? You can restore them with Undo.",
    "zh-Hant": "刪除 {v1} 個映射（#{v2}）？可以用「撤銷」恢復。"
  },
  "无法改绑": {
    "en": "Cannot reassign",
    "zh-Hant": "無法改綁"
  },
  "字段「{v1}」已被映射 #{v2} 使用。请先删除或合并该映射。": {
    "en": "Field “{v1}” is used by mapping #{v2}. Delete or merge that mapping first.",
    "zh-Hant": "字段「{v1}」已被映射 #{v2} 使用。請先刪除或合併該映射。"
  },
  "{v1}映射 #{v2}": {
    "en": "{v1} mapping #{v2}",
    "zh-Hant": "{v1}映射 #{v2}"
  },
  "删除映射 #{v1}？可以用「撤销」恢复。": {
    "en": "Delete mapping #{v1}? You can restore it with Undo.",
    "zh-Hant": "刪除映射 #{v1}？可以用「撤銷」恢復。"
  },
  "删除映射 #{v1}": {
    "en": "Delete mapping #{v1}",
    "zh-Hant": "刪除映射 #{v1}"
  },
  "撤销（Ctrl/⌘+Z）": {
    "en": "Undo (Ctrl/⌘+Z)",
    "zh-Hant": "撤銷（Ctrl/⌘+Z）"
  },
  "↶ 撤销": {
    "en": "↶ Undo",
    "zh-Hant": "↶ 撤銷"
  },
  "重做（Ctrl/⌘+Shift+Z）": {
    "en": "Redo (Ctrl/⌘+Shift+Z)",
    "zh-Hant": "重做（Ctrl/⌘+Shift+Z）"
  },
  "↷ 重做": {
    "en": "↷ Redo",
    "zh-Hant": "↷ 重做"
  },
  "列表可用键盘操作：↑↓ 移动，X 或空格多选，D 启用/禁用，Delete 删除": {
    "en": "Keyboard: ↑↓ to move, X or Space to select, D to enable/disable, Delete to remove",
    "zh-Hant": "列表可用鍵盤操作：↑↓ 移動，X 或空格多選，D 啓用/禁用，Delete 刪除"
  },
  "当前文件的实际取值": {
    "en": "Actual value in current file",
    "zh-Hant": "當前文件的實際取值"
  },
  "映射列表": {
    "en": "Mapping list",
    "zh-Hant": "映射列表"
  },
  "还没有映射": {
    "en": "No mappings yet",
    "zh-Hant": "還沒有映射"
  },
  "目标已失效": {
    "en": "Target is invalid",
    "zh-Hant": "目標已失效"
  },
  "（已禁用）": {
    "en": " (disabled)",
    "zh-Hant": "（已禁用）"
  },
  "映射 #{v1}：{v2}{v3}": {
    "en": "Mapping #{v1}: {v2}{v3}",
    "zh-Hant": "映射 #{v1}：{v2}{v3}"
  },
  "选择映射 #{v1}": {
    "en": "Select mapping #{v1}",
    "zh-Hant": "選擇映射 #{v1}"
  },
  "已禁用": {
    "en": "Disabled",
    "zh-Hant": "已禁用"
  },
  "当前文件无法定位": {
    "en": "Cannot locate in this file",
    "zh-Hant": "當前文件無法定位"
  },
  "把此映射的目标改为当前选中的字段「{v1}」": {
    "en": "Reassign this mapping to selected field “{v1}”",
    "zh-Hant": "把此映射的目標改為當前選中的字段「{v1}」"
  },
  "改绑": {
    "en": "Reassign",
    "zh-Hant": "改綁"
  },
  "目标：": {
    "en": "Target: ",
    "zh-Hant": "目標："
  },
  "（已删除）": {
    "en": "(deleted)",
    "zh-Hant": "（已刪除）"
  },
  "列：": {
    "en": "Columns: ",
    "zh-Hant": "列："
  },
  "组内：": {
    "en": "Within group: ",
    "zh-Hant": "組內："
  },
  "取第一条": {
    "en": "First",
    "zh-Hant": "取第一條"
  },
  "取最后一条": {
    "en": "Last",
    "zh-Hant": "取最後一條"
  },
  "去重拼接": {
    "en": "Join unique",
    "zh-Hant": "去重拼接"
  },
  "计数": {
    "en": "Count",
    "zh-Hant": "計數"
  },
  "转换：": {
    "en": "Transforms: ",
    "zh-Hant": "轉換："
  },
  "批量操作": {
    "en": "Bulk actions",
    "zh-Hant": "批量操作"
  },
  "已选 ": {
    "en": "Selected ",
    "zh-Hant": "已選 "
  },
  "取消选择": {
    "en": "Clear selection",
    "zh-Hant": "取消選擇"
  },
  "模板还没有字段": {
    "en": "This template has no fields yet",
    "zh-Hant": "模板還沒有字段"
  },
  "在右侧模板中选中内容，点击「设为待填区域」": {
    "en": "Select template content on the right and click “Set as fillable region”",
    "zh-Hant": "在右側模板中選中內容，點擊「設為待填區域」"
  },
  "（映射 #{v1}）": {
    "en": " (mapping #{v1})",
    "zh-Hant": "（映射 #{v1}）"
  },
  "（未映射）": {
    "en": " (unmapped)",
    "zh-Hant": "（未映射）"
  },
  "字段「{v1}」{v2}": {
    "en": "Field “{v1}”{v2}",
    "zh-Hant": "字段「{v1}」{v2}"
  },
  "与同名字段共享数据绑定": {
    "en": "Shares the data binding with fields of the same name",
    "zh-Hant": "與同名字段共享數據綁定"
  },
  "共享": {
    "en": "Shared",
    "zh-Hant": "共享"
  },
  "重复明细区域": {
    "en": "Repeating detail region",
    "zh-Hant": "重復明細區域"
  },
  "原内容：「": {
    "en": "Original content: “",
    "zh-Hant": "原內容：「"
  },
  "」（生成时替换；删除字段可恢复）": {
    "en": "” (replaced on generation; remove the field to restore)",
    "zh-Hant": "」（生成時替換；刪除字段可恢復）"
  },
  " 必填（为空时阻止该样本）": {
    "en": " Required (empty value blocks this sample)",
    "zh-Hant": " 必填（為空時阻止該樣本）"
  },
  "数据类型": {
    "en": "Data type",
    "zh-Hant": "數據類型"
  },
  "自动（保持来源类型）": {
    "en": "Automatic (preserve source type)",
    "zh-Hant": "自動（保持來源類型）"
  },
  "文本": {
    "en": "Text",
    "zh-Hant": "文本"
  },
  "数字": {
    "en": "Number",
    "zh-Hant": "數字"
  },
  "日期": {
    "en": "Date",
    "zh-Hant": "日期"
  },
  " 超长导致分页增加时允许缩小字号": {
    "en": " Allow font shrinking if extra pages are caused by long text",
    "zh-Hant": " 超長導致分頁增加時允許縮小字號"
  },
  "最小": {
    "en": "Minimum",
    "zh-Hant": "最小"
  },
  "号": {
    "en": "pt",
    "zh-Hant": "號"
  },
  "字段格式（对所有映射生效）": {
    "en": "Field format (applies to every mapping)",
    "zh-Hant": "字段格式（對所有映射生效）"
  },
  "PDF 明细区域：": {
    "en": "PDF detail region: ",
    "zh-Hant": "PDF 明細區域："
  },
  " 列，行高 ": {
    "en": " columns, row height ",
    "zh-Hant": " 列，行高 "
  },
  " 点，可容纳 ": {
    "en": " pt; capacity ",
    "zh-Hant": " 點，可容納 "
  },
  " 行": {
    "en": " rows",
    "zh-Hant": " 行"
  },
  "表格超出幻灯片时": {
    "en": "When the table runs past the slide",
    "zh-Hant": "表格超出投影片時"
  },
  "照常生成并提示": {
    "en": "Generate it and show a warning",
    "zh-Hant": "照常產生並提示"
  },
  "复制本张幻灯片续写": {
    "en": "Continue on copies of this slide",
    "zh-Hant": "複製本張投影片續寫"
  },
  "表头行在每张幻灯片上重复，表格下方的行只放在最后一张，幻灯片上的其他字段同样填写。按模板中的行高计算：文字换行使行变高时仍可能超出。": {
    "en": "The header rows repeat on every slide, the rows below the table go only on the last one, and the slide's other fields are filled the same on each. Rows are counted at their height in the template, so text that wraps onto more lines can still run past the slide.",
    "zh-Hant": "表頭行在每張投影片上重複，表格下方的行只放在最後一張，投影片上的其他欄位同樣填寫。按模板中的行高計算：文字換行使行變高時仍可能超出。"
  },
  "超过可容纳行数时": {
    "en": "When rows exceed capacity",
    "zh-Hant": "超過可容納行數時"
  },
  "视为错误（该样本不生成）": {
    "en": "Fail this sample",
    "zh-Hant": "視為錯誤（該樣本不生成）"
  },
  "复制本页续写": {
    "en": "Duplicate this page to continue",
    "zh-Hant": "複製本頁續寫"
  },
  "压缩行高放入（最多 50%）": {
    "en": "Compress row height (up to 50%)",
    "zh-Hant": "壓縮行高放入（最多 50%）"
  },
  "零条明细时": {
    "en": "When there are no detail rows",
    "zh-Hant": "零條明細時"
  },
  "区域留空": {
    "en": "Leave region empty",
    "zh-Hant": "區域留空"
  },
  "第一行显示占位文字": {
    "en": "Show placeholder in first row",
    "zh-Hant": "第一行顯示佔位文字"
  },
  "视为错误": {
    "en": "Report an error",
    "zh-Hant": "視為錯誤"
  },
  "占位文字": {
    "en": "Placeholder text",
    "zh-Hant": "佔位文字"
  },
  "（无）": {
    "en": "(none)",
    "zh-Hant": "（無）"
  },
  "明细列：": {
    "en": "Detail columns: ",
    "zh-Hant": "明細列："
  },
  "中": {
    "en": "Center",
    "zh-Hant": "中"
  },
  "重复内容块：": {
    "en": "Repeating block: ",
    "zh-Hant": "重復內容塊："
  },
  " 个段落，每条明细复制一份": {
    "en": " paragraphs, copied once per detail row",
    "zh-Hant": " 個段落，每條明細複製一份"
  },
  "相邻两份之间": {
    "en": "Between copies",
    "zh-Hant": "相鄰兩份之間"
  },
  "直接相连": {
    "en": "No separator",
    "zh-Hant": "直接相連"
  },
  "空一行": {
    "en": "Blank line",
    "zh-Hant": "空一行"
  },
  "分页（每条明细一页）": {
    "en": "Page break (one detail per page)",
    "zh-Hant": "分頁（每條明細一頁）"
  },
  "删除整个内容块": {
    "en": "Remove entire block",
    "zh-Hant": "刪除整個內容塊"
  },
  "保留一份，第一列显示占位文字": {
    "en": "Keep one copy; placeholder in first column",
    "zh-Hant": "保留一份，第一列顯示佔位文字"
  },
  "明细列（在块内划选文字可添加列）：": {
    "en": "Detail columns (select text inside the block to add columns):",
    "zh-Hant": "明細列（在塊內划選文字可添加列）："
  },
  "还没有列。请在右侧内容块中划选要随明细变化的文字，选择「作为内容块的一列」。": {
    "en": "No columns yet. Select text that should vary in the block on the right, then choose “Use as block column”.",
    "zh-Hant": "還沒有列。請在右側內容塊中划選要隨明細變化的文字，選擇「作為內容塊的一列」。"
  },
  "取消该列（恢复为原文字，不随明细变化）": {
    "en": "Remove column (restore original text; no longer varies by detail)",
    "zh-Hant": "取消該列（恢復為原文字，不隨明細變化）"
  },
  "删除明细行，保留表头": {
    "en": "Remove detail rows, keep headers",
    "zh-Hant": "刪除明細行，保留表頭"
  },
  "保留一行空白": {
    "en": "Keep one blank row",
    "zh-Hant": "保留一行空白"
  },
  "保留一行并显示占位文字": {
    "en": "Keep one row with placeholder text",
    "zh-Hant": "保留一行並顯示佔位文字"
  },
  "删除整个表格（仅文档）": {
    "en": "Remove entire table (documents only)",
    "zh-Hant": "刪除整個表格（僅文檔）"
  },
  " 跨页时重复表头（": {
    "en": " Repeat headers across pages (",
    "zh-Hant": " 跨頁時重復表頭（"
  },
  "固定区域：": {
    "en": "Fixed region: ",
    "zh-Hant": "固定區域："
  },
  " 列。来源区域必须同样大小。": {
    "en": " columns. Source region must have the same size.",
    "zh-Hant": " 列。來源區域必須同樣大小。"
  },
  "文字颜色": {
    "en": "Text color",
    "zh-Hant": "文字顏色"
  },
  "（创建时取自原内容）": {
    "en": " (taken from original content)",
    "zh-Hant": "（創建時取自原內容）"
  },
  "字号": {
    "en": "Font size",
    "zh-Hant": "字號"
  },
  "对齐": {
    "en": "Alignment",
    "zh-Hant": "對齊"
  },
  "删除字段": {
    "en": "Delete field",
    "zh-Hant": "刪除字段"
  },
  "尚未定义样本": {
    "en": "No sample rules defined",
    "zh-Hant": "尚未定義樣本"
  },
  "定义样本": {
    "en": "Define samples",
    "zh-Hant": "定義樣本"
  },
  " 条样本": {
    "en": " samples",
    "zh-Hant": " 條樣本"
  },
  "修改规则": {
    "en": "Edit rules",
    "zh-Hant": "修改規則"
  },
  "排除空白行 ": {
    "en": "Empty rows excluded ",
    "zh-Hant": "排除空白行 "
  },
  " · 过滤 ": {
    "en": " · Filtered ",
    "zh-Hant": " · 過濾 "
  },
  " · 分组值为空 ": {
    "en": " · Empty group key ",
    "zh-Hant": " · 分組值為空 "
  },
  " · 使用文件 ": {
    "en": " · Files used ",
    "zh-Hant": " · 使用文件 "
  },
  "显示更多（已显示 {v1} / {v2} 条）": {
    "en": "Show more ({v1} / {v2} shown)",
    "zh-Hant": "顯示更多（已顯示 {v1} / {v2} 條）"
  },
  " 个问题": {
    "en": " issues",
    "zh-Hant": " 個問題"
  },
  "样本错误": {
    "en": "Sample errors",
    "zh-Hant": "樣本錯誤"
  },
  "通过": {
    "en": "Passed",
    "zh-Hant": "通過"
  },
  "结构特征与代表文件不同（仅提示；以下逐项核对为准）": {
    "en": "Structure differs from representative file (informational; see detailed checks below)",
    "zh-Hant": "結構特徵與代表文件不同（僅提示；以下逐項核對為準）"
  },
  "样本 ": {
    "en": "Samples ",
    "zh-Hant": "樣本 "
  },
  " 条（按首条样本核对）": {
    "en": " (checked against the first sample)",
    "zh-Hant": " 條（按首條樣本核對）"
  },
  "在此文件中重新定位": {
    "en": "Relocate in this file",
    "zh-Hant": "在此文件中重新定位"
  },
  "清除该文件的单独定位": {
    "en": "Clear file-specific location",
    "zh-Hant": "清除該文件的單獨定位"
  },
  "映射 #": {
    "en": "Mapping #",
    "zh-Hant": "映射 #"
  },
  " 在此文件": {
    "en": " in this file",
    "zh-Hant": " 在此文件"
  },
  "第 {v1} 块": {
    "en": "Block {v1}",
    "zh-Hant": "第 {v1} 塊"
  },
  "使用单独定位": {
    "en": "Using a file-specific location",
    "zh-Hant": "使用單獨定位"
  },
  "清除": {
    "en": "Clear",
    "zh-Hant": "清除"
  },
  "确认 / 修正识别值": {
    "en": "Confirm / correct recognized value",
    "zh-Hant": "確認 / 修正識別值"
  },
  "识别结果为「{v1}」。请对照左侧原图核对，确认无误直接保存，或改为正确的值：": {
    "en": "Recognized “{v1}”. Compare with the original image on the left, then save to confirm or enter the correct value:",
    "zh-Hant": "識別結果為「{v1}」。請對照左側原圖核對，確認無誤直接保存，或改為正確的值："
  },
  "确认/修正识别值": {
    "en": "Confirm / correct recognized value",
    "zh-Hant": "確認/修正識別值"
  },
  "表格竖线": {
    "en": "Table vertical lines",
    "zh-Hant": "表格竪線"
  },
  "表头文字位置": {
    "en": "Header text positions",
    "zh-Hant": "表頭文字位置"
  },
  "样例行文字": {
    "en": "Sample row text",
    "zh-Hant": "樣例行文字"
  },
  "未找到分列依据（单列）": {
    "en": "No column boundaries found (single column)",
    "zh-Hant": "未找到分列依據（單列）"
  },
  "表格横线": {
    "en": "Table horizontal lines",
    "zh-Hant": "表格橫線"
  },
  "样例行距": {
    "en": "Sample row spacing",
    "zh-Hant": "樣例行距"
  },
  "表头与样例行间距": {
    "en": "Header-to-sample spacing",
    "zh-Hant": "表頭與樣例行間距"
  },
  "默认行高": {
    "en": "Default row height",
    "zh-Hant": "默認行高"
  },
  "行高至少 6 点": {
    "en": "Row height must be at least 6 pt",
    "zh-Hant": "行高至少 6 點"
  },
  "行高超过区域高度": {
    "en": "Row height exceeds region height",
    "zh-Hant": "行高超過區域高度"
  },
  "列「{v1}」太窄": {
    "en": "Column “{v1}” is too narrow",
    "zh-Hant": "列「{v1}」太窄"
  },
  "每一列都需要名称": {
    "en": "Every column needs a name",
    "zh-Hant": "每一列都需要名稱"
  },
  "无法识别明细区域：": {
    "en": "Cannot detect detail region: ",
    "zh-Hant": "無法識別明細區域："
  },
  " 正在识别表格的列和行…": {
    "en": " Detecting table columns and rows…",
    "zh-Hant": " 正在識別表格的列和行…"
  },
  "可容纳 ": {
    "en": "Capacity: ",
    "zh-Hant": "可容納 "
  },
  "列依据：": {
    "en": "Column detection: ",
    "zh-Hant": "列依據："
  },
  "行高依据：": {
    "en": "Row height detection: ",
    "zh-Hant": "行高依據："
  },
  "模板页面": {
    "en": "Template page",
    "zh-Hant": "模板頁面"
  },
  "拖动调整行高": {
    "en": "Drag to adjust row height",
    "zh-Hant": "拖動調整行高"
  },
  "拖动调整列宽；双击合并相邻两列": {
    "en": "Drag to resize columns; double-click to merge adjacent columns",
    "zh-Hant": "拖動調整列寬；雙擊合併相鄰兩列"
  },
  "该区域的文字方向与页面显示方向不同，请用下方数值调整列边界（单位：点）。": {
    "en": "Text orientation differs from page orientation. Adjust column boundaries using the numeric controls below (in points).",
    "zh-Hant": "該區域的文字方向與頁面顯示方向不同，請用下方數值調整列邊界（單位：點）。"
  },
  "第{v1}列名称": {
    "en": "Column {v1} name",
    "zh-Hant": "第{v1}列名稱"
  },
  "第{v1}列左边界（点）": {
    "en": "Column {v1} left boundary (pt)",
    "zh-Hant": "第{v1}列左邊界（點）"
  },
  "左边界（点）": {
    "en": "Left boundary (pt)",
    "zh-Hant": "左邊界（點）"
  },
  "第{v1}列右边界（点）": {
    "en": "Column {v1} right boundary (pt)",
    "zh-Hant": "第{v1}列右邊界（點）"
  },
  "右边界（点）": {
    "en": "Right boundary (pt)",
    "zh-Hant": "右邊界（點）"
  },
  "从中间拆成两列": {
    "en": "Split into two equal columns",
    "zh-Hant": "從中間拆成兩列"
  },
  "拆分": {
    "en": "Split",
    "zh-Hant": "拆分"
  },
  "与第{v1}列合并（同双击分隔线）": {
    "en": "Merge with column {v1} (also available by double-clicking the divider)",
    "zh-Hant": "與第{v1}列合併（同雙擊分隔線）"
  },
  "第{v1}列与第{v2}列合并": {
    "en": "Merge columns {v1} and {v2}",
    "zh-Hant": "第{v1}列與第{v2}列合併"
  },
  "合并右列": {
    "en": "Merge right",
    "zh-Hant": "合併右列"
  },
  "样例：": {
    "en": "Example: ",
    "zh-Hant": "樣例："
  },
  "行高（点）": {
    "en": "Row height (pt)",
    "zh-Hant": "行高（點）"
  },
  "恢复自动识别": {
    "en": "Restore auto-detection",
    "zh-Hant": "恢復自動識別"
  },
  "请填写字段名称": {
    "en": "Enter a field name",
    "zh-Hant": "請填寫字段名稱"
  },
  "明细区域还在识别中": {
    "en": "Detail region detection is still running",
    "zh-Hant": "明細區域還在識別中"
  },
  "设为待填区域": {
    "en": "Set as fillable region",
    "zh-Hant": "設為待填區域"
  },
  "关闭": {
    "en": "Close",
    "zh-Hant": "關閉"
  },
  "当前选择（": {
    "en": "Current selection (",
    "zh-Hant": "當前選擇（"
  },
  "）不能设为字段。请选择文字、单元格、表格行或框选区域。": {
    "en": ") cannot become a field. Select text, a cell, a table row or a rectangular region.",
    "zh-Hant": "）不能設為字段。請選擇文字、單元格、表格行或框選區域。"
  },
  "创建中…": {
    "en": "Creating…",
    "zh-Hant": "創建中…"
  },
  "创建字段": {
    "en": "Create field",
    "zh-Hant": "創建字段"
  },
  "选择内容：": {
    "en": "Selected content: ",
    "zh-Hant": "選擇內容："
  },
  "字段名称": {
    "en": "Field name",
    "zh-Hant": "字段名稱"
  },
  "例如：客户名称": {
    "en": "Example: Customer name",
    "zh-Hant": "例如：客戶名稱"
  },
  "替换选中内容（默认）": {
    "en": "Replace selected content (default)",
    "zh-Hant": "替換選中內容（默認）"
  },
  "保留原文，在": {
    "en": "Keep original text; at ",
    "zh-Hant": "保留原文，在"
  },
  "选区末尾": {
    "en": "the end of the selection",
    "zh-Hant": "選區末尾"
  },
  "此处": {
    "en": "this position",
    "zh-Hant": "此處"
  },
  "插入": {
    "en": "insert",
    "zh-Hant": "插入"
  },
  "替换单元格内容（默认）": {
    "en": "Replace cell content (default)",
    "zh-Hant": "替換單元格內容（默認）"
  },
  "保留原文，在其后追加": {
    "en": "Keep original text; append after it",
    "zh-Hant": "保留原文，在其後追加"
  },
  "保留原文，在其前插入": {
    "en": "Keep original text; insert before it",
    "zh-Hant": "保留原文，在其前插入"
  },
  "上方表头行数（跨页时重复显示）": {
    "en": "Header rows above (repeat on each page)",
    "zh-Hant": "上方表頭行數（跨頁時重復顯示）"
  },
  "保留一行并显示「（无）」": {
    "en": "Keep one row showing “(none)”",
    "zh-Hant": "保留一行並顯示「（無）」"
  },
  "删除整个表格": {
    "en": "Remove entire table",
    "zh-Hant": "刪除整個表格"
  },
  "视为错误（阻止该样本）": {
    "en": "Fail this sample",
    "zh-Hant": "視為錯誤（阻止該樣本）"
  },
  " 自动把「标签：值」中的值设为明细列（如「品名：示例商品」→ 列「品名」）": {
    "en": " Automatically use values in “Label: value” as detail columns (e.g. “Product: example” → column “Product”)",
    "zh-Hant": " 自動把「標籤：值」中的值設為明細列（如「品名：示例商品」→ 列「品名」）"
  },
  "保留一份，第一列显示「（无）」": {
    "en": "Keep one copy with “(none)” in the first column",
    "zh-Hant": "保留一份，第一列顯示「（無）」"
  },
  "明细超过 ": {
    "en": "When detail exceeds ",
    "zh-Hant": "明細超過 "
  },
  " 行时": {
    "en": " rows",
    "zh-Hant": " 行時"
  },
  "复制本页续写（页上其他字段同样填写）": {
    "en": "Duplicate this page to continue (other fields are also filled)",
    "zh-Hant": "複製本頁續寫（頁上其他字段同樣填寫）"
  },
  "压缩行高放入（最多压缩到 50%）": {
    "en": "Compress row height (down to 50%)",
    "zh-Hant": "壓縮行高放入（最多壓縮到 50%）"
  },
  "第一行显示「（无）」": {
    "en": "Show “(none)” in the first row",
    "zh-Hant": "第一行顯示「（無）」"
  },
  "字号（留空=沿用原文字号）": {
    "en": "Font size (empty = retain original)",
    "zh-Hant": "字號（留空=沿用原文字號）"
  },
  "最小字号": {
    "en": "Minimum font size",
    "zh-Hant": "最小字號"
  },
  "新内容放不下时逐步缩小到最小字号，仍放不下则报错，不会截断。": {
    "en": "Text that does not fit shrinks to the minimum size. If it still cannot fit, generation fails rather than truncating.",
    "zh-Hant": "新內容放不下時逐步縮小到最小字號，仍放不下則報錯，不會截斷。"
  },
  " 必填（值为空时阻止生成该样本）": {
    "en": " Required (empty value blocks generation for this sample)",
    "zh-Hant": " 必填（值為空時阻止生成該樣本）"
  },
  "这 {v1} 个段落作为一个整体：每条明细复制一份（保留格式、编号和图片）": {
    "en": "Treat these {v1} paragraphs as one block: copy once per detail row, retaining formatting, numbering and images",
    "zh-Hant": "這 {v1} 個段落作為一個整體：每條明細複製一份（保留格式、編號和圖片）"
  },
  "文字字段": {
    "en": "Text field",
    "zh-Hant": "文字字段"
  },
  "在光标处插入字段": {
    "en": "Insert field at cursor",
    "zh-Hant": "在光標處插入字段"
  },
  "选中的文字将变成字段（可跨多个格式片段）": {
    "en": "Turn selected text into a field (may span multiple formatting runs)",
    "zh-Hant": "選中的文字將變成字段（可跨多個格式片段）"
  },
  "作为内容块「{v1}」的一列": {
    "en": "Use as a column of block “{v1}”",
    "zh-Hant": "作為內容塊「{v1}」的一列"
  },
  "每一份中这里填入该条明细对应列的值": {
    "en": "Each copy uses this detail row's value for this column",
    "zh-Hant": "每一份中這裡填入該條明細對應列的值"
  },
  "每一份中这里都填入同一个值（不随明细变化）": {
    "en": "Every copy uses the same value here (does not vary by detail)",
    "zh-Hant": "每一份中這裡都填入同一個值（不隨明細變化）"
  },
  "单元格字段": {
    "en": "Cell field",
    "zh-Hant": "單元格字段"
  },
  "整个单元格的文字作为字段": {
    "en": "Use all text in this cell as a field",
    "zh-Hant": "整個單元格的文字作為字段"
  },
  "每条明细复制一次该行，保留该行样式；每个单元格成为一个明细列": {
    "en": "Copy this row once per detail, retaining styling; each cell becomes a detail column",
    "zh-Hant": "每條明細複製一次該行，保留該行樣式；每個單元格成為一個明細列"
  },
  "{v1} 行 × {v2} 列，按行列一一对应填入": {
    "en": "{v1} rows × {v2} columns, filled cell by cell",
    "zh-Hant": "{v1} 行 × {v2} 列，按行列一一對應填入"
  },
  "{v1}：{v2} 行 × {v3} 列，按行列一一对应": {
    "en": "{v1}: {v2} rows × {v3} columns, matched cell by cell",
    "zh-Hant": "{v1}：{v2} 行 × {v3} 列，按行列一一對應"
  },
  "第 {v1} 行的 {v2} 为明细模板行：有几条明细就插入几行（公式、合并单元格和条件格式随之平移）": {
    "en": "{v2} on row {v1} is the detail template row: insert one row per detail, shifting formulas, merged cells and conditional formatting",
    "zh-Hant": "第 {v1} 行的 {v2} 為明細模板行：有幾條明細就插入幾行（公式、合併單元格和條件格式隨之平移）"
  },
  "PDF 区域字段（单个值）": {
    "en": "PDF region field (single value)",
    "zh-Hant": "PDF 區域字段（單個值）"
  },
  "删除框内原文字，生成时写入新值": {
    "en": "Remove original text in the box and insert the new value during generation",
    "zh-Hant": "刪除框內原文字，生成時寫入新值"
  },
  "重复明细区域（多行）": {
    "en": "Repeating detail region (multiple rows)",
    "zh-Hant": "重復明細區域（多行）"
  },
  "框选明细表的数据行（不含表头）：每条明细写入一行，自动识别列和行高，可拖动调整": {
    "en": "Select detail data rows, excluding the header. One row per detail; columns and row height are detected automatically and can be adjusted by dragging.",
    "zh-Hant": "框選明細表的數據行（不含表頭）：每條明細寫入一行，自動識別列和行高，可拖動調整"
  },
  "使用 PDF 已定义的表单字段": {
    "en": "Use an existing PDF form field",
    "zh-Hant": "使用 PDF 已定義的表單字段"
  },
  "文本框中选中的文字": {
    "en": "Selected text in a text box",
    "zh-Hant": "文本框中選中的文字"
  },
  "表格单元格": {
    "en": "Table cell",
    "zh-Hant": "表格單元格"
  },
  "按明细条数复制该表格行（幻灯片不会自动分页）": {
    "en": "Copy this table row for each detail (slides do not paginate automatically)",
    "zh-Hant": "按明細條數複製該表格行（幻燈片不會自動分頁）"
  },
  "已导入模板「{v1}」": {
    "en": "Imported template “{v1}”",
    "zh-Hant": "已導入模板「{v1}」"
  },
  "设置目标模板": {
    "en": "Set target template",
    "zh-Hant": "設置目標模板"
  },
  "上传已有文件作为模板": {
    "en": "Upload an existing file",
    "zh-Hant": "上傳已有文件作為模板"
  },
  "在软件内新建模板": {
    "en": "Create a template in the editor",
    "zh-Hant": "在軟件內新建模板"
  },
  "从模板库选择": {
    "en": "Choose from template library",
    "zh-Hant": "從模板庫選擇"
  },
  "上传中 {v1}%…": {
    "en": "Uploading {v1}%…",
    "zh-Hant": "上傳中 {v1}%…"
  },
  "拖入模板文件，或 ": {
    "en": "Drop a template file, or ",
    "zh-Hant": "拖入模板文件，或 "
  },
  "点击选择": {
    "en": "choose a file",
    "zh-Hant": "點擊選擇"
  },
  "支持 DOCX、XLSX、PDF、PPTX，以及 PNG / JPEG 图片（直接输出同尺寸图片）；DOC/XLS/PPT/ODT/ODS/ODP/RTF 会先经格式转换（会提示可能的版式变化），BMP/TIFF/WEBP 输出为 PNG": {
    "en": "Supports DOCX, XLSX, PDF, PPTX and PNG/JPEG images (same-size image output). DOC/XLS/PPT/ODT/ODS/ODP/RTF are converted first, with layout warnings. BMP/TIFF/WEBP output as PNG.",
    "zh-Hant": "支持 DOCX、XLSX、PDF、PPTX，以及 PNG / JPEG 圖片（直接輸出同尺寸圖片）；DOC/XLS/PPT/ODT/ODS/ODP/RTF 會先經格式轉換（會提示可能的版式變化），BMP/TIFF/WEBP 輸出為 PNG"
  },
  "📄 文档型模板（生成 DOCX）": {
    "en": "📄 Document template (DOCX output)",
    "zh-Hant": "📄 文檔型模板（生成 DOCX）"
  },
  "新建文档模板": {
    "en": "New document template",
    "zh-Hant": "新建文檔模板"
  },
  "▦ 表格型模板（生成 XLSX / CSV）": {
    "en": "▦ Spreadsheet template (XLSX / CSV output)",
    "zh-Hant": "▦ 表格型模板（生成 XLSX / CSV）"
  },
  "新建表格模板": {
    "en": "New spreadsheet template",
    "zh-Hant": "新建表格模板"
  },
  "更新时间": {
    "en": "Updated",
    "zh-Hant": "更新時間"
  },
  "当前": {
    "en": "Current",
    "zh-Hant": "當前"
  },
  "上传：{v1}": {
    "en": "Uploaded: {v1}",
    "zh-Hant": "上傳：{v1}"
  },
  "使用": {
    "en": "Use",
    "zh-Hant": "使用"
  },
  "复制一份后使用，避免影响其他项目": {
    "en": "Duplicate before using to avoid affecting other projects",
    "zh-Hant": "複製一份後使用，避免影響其他項目"
  },
  "复制后使用": {
    "en": "Duplicate and use",
    "zh-Hant": "複製後使用"
  },
  "加载模板库…": {
    "en": "Loading template library…",
    "zh-Hant": "加載模板庫…"
  },
  "模板库为空": {
    "en": "Template library is empty",
    "zh-Hant": "模板庫為空"
  },
  "内容已在其他窗口或被其他人修改，这次操作没有执行。已刷新为最新内容，请在此基础上重新操作。": {
    "en": "Someone else or another window changed this content. Your action was not applied. The latest content is loaded; apply your change again.",
    "zh-Hant": "內容已在其他窗口或被其他人修改，這次操作沒有執行。已刷新為最新內容，請在此基礎上重新操作。"
  },
  "已撤销映射修改": {
    "en": "Mapping change undone",
    "zh-Hant": "已撤銷映射修改"
  },
  "已撤销模板修改": {
    "en": "Template change undone",
    "zh-Hant": "已撤銷模板修改"
  },
  "查找结果：{v1}": {
    "en": "Search result: {v1}",
    "zh-Hant": "查找結果：{v1}"
  },
  "映射 #{v1}": {
    "en": "Mapping #{v1}",
    "zh-Hant": "映射 #{v1}"
  },
  "字段「{v1}」": {
    "en": "Field “{v1}”",
    "zh-Hant": "字段「{v1}」"
  },
  "「{v1}」没有找到": {
    "en": "“{v1}” was not found",
    "zh-Hant": "「{v1}」沒有找到"
  },
  "无法从当前选择生成定位": {
    "en": "Cannot create a locator from this selection",
    "zh-Hant": "無法從當前選擇生成定位"
  },
  "已为「{v1}」单独设置映射 #{v2} 的定位": {
    "en": "Set a location for mapping #{v2} in “{v1}”",
    "zh-Hant": "已為「{v1}」單獨設置映射 #{v2} 的定位"
  },
  "删除字段「": {
    "en": "Delete field “",
    "zh-Hant": "刪除字段「"
  },
  "」并恢复模板中原有内容？": {
    "en": "” and restore the original template content?",
    "zh-Hant": "」並恢復模板中原有內容？"
  },
  "同时会删除 ": {
    "en": "This also deletes ",
    "zh-Hant": "同時會刪除 "
  },
  " 个相关映射": {
    "en": " related mappings",
    "zh-Hant": " 個相關映射"
  },
  "可以撤销。": {
    "en": "You can undo this.",
    "zh-Hant": "可以撤銷。"
  },
  "样本规则已保存": {
    "en": "Sample rules saved",
    "zh-Hant": "樣本規則已保存"
  },
  "，其中 {v1} 个低置信度（红色下划线）": {
    "en": ", including {v1} low-confidence words (red underline)",
    "zh-Hant": "，其中 {v1} 個低置信度（紅色下划線）"
  },
  "OCR 完成：识别 {v1} 个词{v2}": {
    "en": "OCR complete: recognized {v1} words{v2}",
    "zh-Hant": "OCR 完成：識別 {v1} 個詞{v2}"
  },
  "返回项目列表": {
    "en": "Back to projects",
    "zh-Hant": "返回項目列表"
  },
  "上传来源": {
    "en": "Upload sources",
    "zh-Hant": "上傳來源"
  },
  "设置模板": {
    "en": "Set template",
    "zh-Hant": "設置模板"
  },
  "预览生成": {
    "en": "Preview output",
    "zh-Hant": "預覽生成"
  },
  "该映射的目标字段已不存在，请删除或重新建立": {
    "en": "This mapping's target field no longer exists. Delete or recreate the mapping.",
    "zh-Hant": "該映射的目標字段已不存在，請刪除或重新建立"
  },
  "工作流": {
    "en": "Workflow",
    "zh-Hant": "工作流"
  },
  "上传来源文件…": {
    "en": "Upload source files…",
    "zh-Hant": "上傳來源文件…"
  },
  "选择要上传的文件": {
    "en": "Choose files to upload",
    "zh-Hant": "選擇要上傳的文件"
  },
  "设置 / 更换模板": {
    "en": "Set / change template",
    "zh-Hant": "設置 / 更換模板"
  },
  "定义样本规则": {
    "en": "Define sample rules",
    "zh-Hant": "定義樣本規則"
  },
  "未定义": {
    "en": "Not defined",
    "zh-Hant": "未定義"
  },
  "先选中左侧来源和右侧字段": {
    "en": "Select a source on the left and a field on the right first",
    "zh-Hant": "先選中左側來源和右側字段"
  },
  "当前映射 #{v1}": {
    "en": "Current mapping #{v1}",
    "zh-Hant": "當前映射 #{v1}"
  },
  "删除映射 #{v1}…": {
    "en": "Delete mapping #{v1}…",
    "zh-Hant": "刪除映射 #{v1}…"
  },
  "可以撤销": {
    "en": "Undo available",
    "zh-Hant": "可以撤銷"
  },
  "关闭预览": {
    "en": "Close preview",
    "zh-Hant": "關閉預覽"
  },
  "打开预览": {
    "en": "Open preview",
    "zh-Hant": "打開預覽"
  },
  "批量生成…": {
    "en": "Batch generation…",
    "zh-Hant": "批量生成…"
  },
  "撤销": {
    "en": "Undo",
    "zh-Hant": "撤銷"
  },
  "清除当前选择": {
    "en": "Clear current selection",
    "zh-Hant": "清除當前選擇"
  },
  "在来源文件中查找": {
    "en": "Find in source file",
    "zh-Hant": "在來源文件中查找"
  },
  "重新 OCR 识别当前文件": {
    "en": "Run OCR again on current file",
    "zh-Hant": "重新 OCR 識別當前文件"
  },
  "OCR 识别当前文件": {
    "en": "Run OCR on current file",
    "zh-Hant": "OCR 識別當前文件"
  },
  "面板": {
    "en": "Panels",
    "zh-Hant": "面板"
  },
  "查看映射列表": {
    "en": "View mappings",
    "zh-Hant": "查看映射列表"
  },
  "查看模板字段": {
    "en": "View template fields",
    "zh-Hant": "查看模板字段"
  },
  "查看样本列表": {
    "en": "View samples",
    "zh-Hant": "查看樣本列表"
  },
  "查看逐文件校验": {
    "en": "View per-file validation",
    "zh-Hant": "查看逐文件校驗"
  },
  "展开右侧面板": {
    "en": "Expand right panel",
    "zh-Hant": "展開右側面板"
  },
  "收起右侧面板（聚焦文档）": {
    "en": "Collapse right panel (focus on document)",
    "zh-Hant": "收起右側面板（聚焦文檔）"
  },
  "收起文件列表": {
    "en": "Collapse file list",
    "zh-Hant": "收起文件列表"
  },
  "隐藏映射连线": {
    "en": "Hide mapping connectors",
    "zh-Hant": "隱藏映射連線"
  },
  "显示映射连线": {
    "en": "Show mapping connectors",
    "zh-Hant": "顯示映射連線"
  },
  "恢复默认布局宽度": {
    "en": "Reset layout widths",
    "zh-Hant": "恢復默認佈局寬度"
  },
  "缩放恢复 100%": {
    "en": "Reset zoom to 100%",
    "zh-Hant": "縮放恢復 100%"
  },
  "帮助": {
    "en": "Help",
    "zh-Hant": "說明"
  },
  "显示键盘快捷键": {
    "en": "Show keyboard shortcuts",
    "zh-Hant": "顯示鍵盤快捷鍵"
  },
  "保存为回归用例…": {
    "en": "Save as regression case…",
    "zh-Hant": "保存為回歸用例…"
  },
  "生成脱敏副本…": {
    "en": "Create anonymized copies…",
    "zh-Hant": "生成脫敏副本…"
  },
  "打开最近的生成任务": {
    "en": "Open latest generation job",
    "zh-Hant": "打開最近的生成任務"
  },
  "回归用例列表": {
    "en": "Regression cases",
    "zh-Hant": "回歸用例列表"
  },
  "切换来源文件": {
    "en": "Switch source file",
    "zh-Hant": "切換來源文件"
  },
  "模板：": {
    "en": "Template: ",
    "zh-Hant": "模板："
  },
  "更换或设置模板": {
    "en": "Change or set template",
    "zh-Hant": "更換或設置模板"
  },
  "样本：": {
    "en": "Samples: ",
    "zh-Hant": "樣本："
  },
  "只读": {
    "en": "Read only",
    "zh-Hant": "只讀"
  },
  "✓ 已自动保存": {
    "en": "✓ Saved automatically",
    "zh-Hant": "✓ 已自動保存"
  },
  "保存失败": {
    "en": "Save failed",
    "zh-Hant": "保存失敗"
  },
  "最近任务：": {
    "en": "Latest job: ",
    "zh-Hant": "最近任務："
  },
  "命令面板：所有操作与快捷键": {
    "en": "Command palette: all actions and shortcuts",
    "zh-Hant": "命令面板：所有操作與快捷鍵"
  },
  "打开命令面板": {
    "en": "Open command palette",
    "zh-Hant": "打開命令面板"
  },
  "回归用例": {
    "en": "Regression cases",
    "zh-Hant": "回歸用例"
  },
  "👁 预览": {
    "en": "👁 Preview",
    "zh-Hant": "👁 預覽"
  },
  "▶ 生成": {
    "en": "▶ Generate",
    "zh-Hant": "▶ 生成"
  },
  "（已完成）": {
    "en": "(completed)",
    "zh-Hant": "（已完成）"
  },
  "显示连线": {
    "en": "Show connectors",
    "zh-Hant": "顯示連線"
  },
  "来源文件": {
    "en": "Source files",
    "zh-Hant": "來源文件"
  },
  "查找…（⌘F）": {
    "en": "Find… (⌘F)",
    "zh-Hant": "查找…（⌘F）"
  },
  "在当前文件中查找": {
    "en": "Find in current file",
    "zh-Hant": "在當前文件中查找"
  },
  "上一个": {
    "en": "Previous",
    "zh-Hant": "上一個"
  },
  "上一个查找结果": {
    "en": "Previous match",
    "zh-Hant": "上一個查找結果"
  },
  "下一个": {
    "en": "Next",
    "zh-Hant": "下一個"
  },
  "下一个查找结果": {
    "en": "Next match",
    "zh-Hant": "下一個查找結果"
  },
  "识别中…": {
    "en": "Recognizing…",
    "zh-Hant": "識別中…"
  },
  "重新 OCR": {
    "en": "Run OCR again",
    "zh-Hant": "重新 OCR"
  },
  "OCR 识别": {
    "en": "Run OCR",
    "zh-Hant": "OCR 識別"
  },
  "缩小来源文件": {
    "en": "Zoom out source",
    "zh-Hant": "縮小來源文件"
  },
  "放大来源文件": {
    "en": "Zoom in source",
    "zh-Hant": "放大來源文件"
  },
  "适合宽度": {
    "en": "Fit width",
    "zh-Hant": "適合寬度"
  },
  "适合": {
    "en": "Fit",
    "zh-Hant": "適合"
  },
  "上一块": {
    "en": "Previous block",
    "zh-Hant": "上一塊"
  },
  "选择表单块": {
    "en": "Select form block",
    "zh-Hant": "選擇表單塊"
  },
  "下一块": {
    "en": "Next block",
    "zh-Hant": "下一塊"
  },
  "该文件中没有找到表单块标记": {
    "en": "No form block marker found in this file",
    "zh-Hant": "該文件中沒有找到表單塊標記"
  },
  "重新定位映射 #": {
    "en": "Relocate mapping #",
    "zh-Hant": "重新定位映射 #"
  },
  "：请在「": {
    "en": ": select the correct location in “",
    "zh-Hant": "：請在「"
  },
  "」中选中正确的位置，然后点击保存。": {
    "en": "”, then save.",
    "zh-Hant": "」中選中正確的位置，然後點擊保存。"
  },
  "保存为此文件的定位": {
    "en": "Save location for this file",
    "zh-Hant": "保存為此文件的定位"
  },
  "调整来源与模板两侧的宽度": {
    "en": "Resize source and template panes",
    "zh-Hant": "調整來源與模板兩側的寬度"
  },
  "拖动或用 ← / → 调整左右宽度，Enter 恢复默认": {
    "en": "Drag or use ← / → to resize; Enter to reset",
    "zh-Hant": "拖動或用 ← / → 調整左右寬度，Enter 恢復默認"
  },
  "目标模板": {
    "en": "Target template",
    "zh-Hant": "目標模板"
  },
  "撤销模板修改": {
    "en": "Undo template change",
    "zh-Hant": "撤銷模板修改"
  },
  "重做模板修改": {
    "en": "Redo template change",
    "zh-Hant": "重做模板修改"
  },
  "编辑模板": {
    "en": "Edit template",
    "zh-Hant": "編輯模板"
  },
  "下载带字段标记的工作副本": {
    "en": "Download working copy with field markers",
    "zh-Hant": "下載帶字段標記的工作副本"
  },
  "工作副本": {
    "en": "Working copy",
    "zh-Hant": "工作副本"
  },
  "下载未修改的原始模板": {
    "en": "Download unchanged original template",
    "zh-Hant": "下載未修改的原始模板"
  },
  "原始": {
    "en": "Original",
    "zh-Hant": "原始"
  },
  "缩小模板": {
    "en": "Zoom out template",
    "zh-Hant": "縮小模板"
  },
  "放大模板": {
    "en": "Zoom in template",
    "zh-Hant": "放大模板"
  },
  "PDF 表单字段：": {
    "en": "PDF form field: ",
    "zh-Hant": "PDF 表單字段："
  },
  "这个项目原来使用的模板已被删除。": {
    "en": "This project's previous template was deleted.",
    "zh-Hant": "這個項目原來使用的模板已被刪除。"
  },
  "这个项目使用的模板未通过安全检查，已被拒绝并隔离。": {
    "en": "This project's template failed the security check and has been rejected and quarantined.",
    "zh-Hant": "這個項目使用的模板未通過安全檢查，已被拒絕並隔離。"
  },
  "需要有编辑权限的成员重新选择模板。": {
    "en": "A member with edit access must select a replacement.",
    "zh-Hant": "需要有編輯權限的成員重新選擇模板。"
  },
  "请重新选择或新建一个模板。": {
    "en": "Choose or create a template again.",
    "zh-Hant": "請重新選擇或新建一個模板。"
  },
  "清除单独定位": {
    "en": "Clear file-specific location",
    "zh-Hant": "清除單獨定位"
  },
  "在左侧选中数据（单元格、区域、文字或 PDF 框选）": {
    "en": "Select data on the left (cell, region, text or PDF box)",
    "zh-Hant": "在左側選中數據（單元格、區域、文字或 PDF 框選）"
  },
  "明确这几行是「每行一份」还是「写入同一份文档的明细」": {
    "en": "Specify whether these rows are separate samples or details in a single document",
    "zh-Hant": "明確這幾行是「每行一份」還是「寫入同一份文檔的明細」"
  },
  "选中区域的含义…": {
    "en": "What does this selection represent?…",
    "zh-Hant": "選中區域的含義…"
  },
  "→ 目标：": {
    "en": "→ Target: ",
    "zh-Hant": "→ 目標："
  },
  "在右侧点选字段，或选中内容后设为待填区域": {
    "en": "Select a field on the right, or select content and mark it as fillable",
    "zh-Hant": "在右側點選字段，或選中內容後設為待填區域"
  },
  "✎ 设为待填区域": {
    "en": "✎ Set as fillable region",
    "zh-Hant": "✎ 設為待填區域"
  },
  "需要同时选中左侧来源和右侧字段": {
    "en": "Select both a source on the left and a field on the right",
    "zh-Hant": "需要同時選中左側來源和右側字段"
  },
  "⇄ 建立映射": {
    "en": "⇄ Create mapping",
    "zh-Hant": "⇄ 建立映射"
  },
  "清除选择": {
    "en": "Clear selection",
    "zh-Hant": "清除選擇"
  },
  "修改映射": {
    "en": "Edit mapping",
    "zh-Hant": "修改映射"
  },
  "映射已保存": {
    "en": "Mapping saved",
    "zh-Hant": "映射已保存"
  },
  "点击修改项目名称": {
    "en": "Click to rename project",
    "zh-Hant": "點擊修改項目名稱"
  },
  "编码与分隔符": {
    "en": "Encoding and delimiter",
    "zh-Hant": "編碼與分隔符"
  },
  "CSV 设置（": {
    "en": "CSV settings (",
    "zh-Hant": "CSV 設置（"
  },
  "CSV 解析设置": {
    "en": "CSV parsing settings",
    "zh-Hant": "CSV 解析設置"
  },
  "已重新解析": {
    "en": "Reparsed",
    "zh-Hant": "已重新解析"
  },
  "重新解析": {
    "en": "Reparse",
    "zh-Hant": "重新解析"
  },
  "编码": {
    "en": "Encoding",
    "zh-Hant": "編碼"
  },
  "自动识别": {
    "en": "Auto-detect",
    "zh-Hant": "自動識別"
  },
  "逗号 ,": {
    "en": "Comma ,",
    "zh-Hant": "逗號 ,"
  },
  "分号 ;": {
    "en": "Semicolon ;",
    "zh-Hant": "分號 ;"
  },
  "竖线 |": {
    "en": "Pipe |",
    "zh-Hant": "竪線 |"
  },
  "选中的 {v1} 行数据，代表什么？": {
    "en": "What do these {v1} selected rows represent?",
    "zh-Hant": "選中的 {v1} 行數據，代表什麼？"
  },
  "每行是一条样本 → 生成 ": {
    "en": "Each row is a sample → generate ",
    "zh-Hant": "每行是一條樣本 → 生成 "
  },
  "整个区域是当前样本的明细表 → 每个文件生成 1 份（共 ": {
    "en": "The entire region is one sample's detail table → one output per file (total: ",
    "zh-Hant": "整個區域是當前樣本的明細表 → 每個文件生成 1 份（共 "
  },
  " 份）": {
    "en": ")",
    "zh-Hant": " 份）"
  },
  "这些行一起写入同一份文档的重复明细行；设置后请把该区域（含表头）映射到模板的重复明细字段。": {
    "en": "These rows populate repeating details in the same document. After setting this rule, map the region including headers to a repeating detail field.",
    "zh-Hant": "這些行一起寫入同一份文檔的重復明細行；設置後請把該區域（含表頭）映射到模板的重復明細字段。"
  },
  "全部": {
    "en": "All",
    "zh-Hant": "全部"
  },
  "跳过": {
    "en": "Skip",
    "zh-Hant": "跳過"
  },
  "任务已经结束，无需取消": {
    "en": "This job has ended; no cancellation needed",
    "zh-Hant": "任務已經結束，無需取消"
  },
  "已经请求过取消，正在停止…": {
    "en": "Cancellation was already requested; stopping…",
    "zh-Hant": "已經請求過取消，正在停止…"
  },
  "已请求取消，正在停止…": {
    "en": "Cancellation requested; stopping…",
    "zh-Hant": "已請求取消，正在停止…"
  },
  "已重新排队失败项": {
    "en": "Failed items requeued",
    "zh-Hant": "已重新排隊失敗項"
  },
  "已重新排队未完成项": {
    "en": "Unfinished items requeued",
    "zh-Hant": "已重新排隊未完成項"
  },
  "（约 {v1} 秒后再试）": {
    "en": " (try again in about {v1} seconds)",
    "zh-Hant": "（約 {v1} 秒後再試）"
  },
  "返回项目": {
    "en": "Back to project",
    "zh-Hant": "返回項目"
  },
  "Synwevo Docs 首页": {
    "en": "Synwevo Docs home",
    "zh-Hant": "Synwevo Docs 首頁"
  },
  "← 返回项目": {
    "en": "← Back to project",
    "zh-Hant": "← 返回項目"
  },
  "生成任务": {
    "en": "Generation job",
    "zh-Hant": "生成任務"
  },
  "规划中": {
    "en": "Planning",
    "zh-Hant": "規劃中"
  },
  "模板「": {
    "en": "Template “",
    "zh-Hant": "模板「"
  },
  " · 来源文件 ": {
    "en": " · Source files ",
    "zh-Hant": " · 來源文件 "
  },
  " 个 · 格式 ": {
    "en": " · Formats ",
    "zh-Hant": " 個 · 格式 "
  },
  " · 创建于 ": {
    "en": " · Created ",
    "zh-Hant": " · 創建於 "
  },
  "正在请求取消…": {
    "en": "Requesting cancellation…",
    "zh-Hant": "正在請求取消…"
  },
  "取消任务": {
    "en": "Cancel job",
    "zh-Hant": "取消任務"
  },
  "正在排队…": {
    "en": "Queuing…",
    "zh-Hant": "正在排隊…"
  },
  "仅重试失败项": {
    "en": "Retry failed only",
    "zh-Hant": "僅重試失敗項"
  },
  "重试失败、已取消以及尚未规划的样本；不包含跳过项": {
    "en": "Retry failed, cancelled and unplanned samples; skipped items are excluded",
    "zh-Hant": "重試失敗、已取消以及尚未規劃的樣本；不包含跳過項"
  },
  "继续未完成项": {
    "en": "Continue unfinished",
    "zh-Hant": "繼續未完成項"
  },
  "正在取消：不再开始新的样本，进行中的一份完成后停止并保留结果。": {
    "en": "Cancelling: no new samples will start. The current sample finishes and its results are retained.",
    "zh-Hant": "正在取消：不再開始新的樣本，進行中的一份完成後停止並保留結果。"
  },
  "正在检查样本并规划本次任务": {
    "en": "Checking samples and planning this job",
    "zh-Hant": "正在檢查樣本並規劃本次任務"
  },
  "：已规划 {v1} / {v2} 个样本": {
    "en": ": planned {v1} / {v2} samples",
    "zh-Hant": "：已規劃 {v1} / {v2} 個樣本"
  },
  "。规划完成后开始生成。": {
    "en": ". Generation starts after planning finishes.",
    "zh-Hant": "。規劃完成後開始生成。"
  },
  "规划进度": {
    "en": "Planning progress",
    "zh-Hant": "規劃進度"
  },
  "生成进度": {
    "en": "Generation progress",
    "zh-Hant": "生成進度"
  },
  "已规划 {v1} / {v2}": {
    "en": "Planned {v1} / {v2}",
    "zh-Hant": "已規劃 {v1} / {v2}"
  },
  "已处理 {v1} / {v2}": {
    "en": "Processed {v1} / {v2}",
    "zh-Hant": "已處理 {v1} / {v2}"
  },
  "跳过（生成前错误）": {
    "en": "Skipped (errors before generation)",
    "zh-Hant": "跳過（生成前錯誤）"
  },
  "取消 / 等待": {
    "en": "Cancelled / waiting",
    "zh-Hant": "取消 / 等待"
  },
  "⬇ 下载全部（ZIP）": {
    "en": "⬇ Download all (ZIP)",
    "zh-Hant": "⬇ 下載全部（ZIP）"
  },
  "仅 ": {
    "en": "Only ",
    "zh-Hant": "僅 "
  },
  "⬇ 结果清单与失败说明（CSV）": {
    "en": "⬇ Results and failure details (CSV)",
    "zh-Hant": "⬇ 結果清單與失敗說明（CSV）"
  },
  "按状态筛选": {
    "en": "Filter by status",
    "zh-Hant": "按狀態篩選"
  },
  "样本列表加载失败：": {
    "en": "Could not load samples: ",
    "zh-Hant": "樣本列表加載失敗："
  },
  "状态": {
    "en": "Status",
    "zh-Hant": "狀態"
  },
  "说明": {
    "en": "Details",
    "zh-Hant": "說明"
  },
  "还有 {v1} 条": {
    "en": "{v1} more",
    "zh-Hant": "還有 {v1} 條"
  },
  "规划完成后显示样本": {
    "en": "Samples appear after planning finishes",
    "zh-Hant": "規劃完成後顯示樣本"
  },
  "没有记录": {
    "en": "No records",
    "zh-Hant": "沒有記錄"
  },
  "← 首页": {
    "en": "← Home",
    "zh-Hant": "← 首頁"
  },
  "第三方组件": {
    "en": "Third-party components",
    "zh-Hant": "第三方組件"
  },
  "组件": {
    "en": "Component",
    "zh-Hant": "組件"
  },
  "许可证": {
    "en": "License",
    "zh-Hant": "許可證"
  },
  "用途": {
    "en": "Purpose",
    "zh-Hant": "用途"
  },
  "删除回归用例": {
    "en": "Delete regression case",
    "zh-Hant": "刪除回歸用例"
  },
  "删除「{v1}」？其中保存的文件和期望结果会一起删除。": {
    "en": "Delete “{v1}”? Its saved files and expected results will also be deleted.",
    "zh-Hant": "刪除「{v1}」？其中保存的文件和期望結果會一起刪除。"
  },
  "全部运行": {
    "en": "Run all",
    "zh-Hant": "全部運行"
  },
  "回归用例加载失败：": {
    "en": "Could not load regression cases: ",
    "zh-Hant": "回歸用例加載失敗："
  },
  "还没有回归用例": {
    "en": "No regression cases yet",
    "zh-Hant": "還沒有回歸用例"
  },
  "✓ 全部一致（{v1}/{v2}）": {
    "en": "✓ All match ({v1}/{v2})",
    "zh-Hant": "✓ 全部一致（{v1}/{v2}）"
  },
  "✖ {v1} 个样本有差异": {
    "en": "✖ {v1} samples differ",
    "zh-Hant": "✖ {v1} 個樣本有差異"
  },
  "运行中…": {
    "en": "Running…",
    "zh-Hant": "運行中…"
  },
  "运行": {
    "en": "Run",
    "zh-Hant": "運行"
  },
  " · 模板「": {
    "en": " · Template “",
    "zh-Hant": " · 模板「"
  },
  "）· 来源 ": {
    "en": ") · Sources ",
    "zh-Hant": "）· 來源 "
  },
  " 个 · 样本 ": {
    "en": " · Samples ",
    "zh-Hant": " 個 · 樣本 "
  },
  " 处差异": {
    "en": " differences",
    "zh-Hant": " 處差異"
  },
  "来源导入与提取": {
    "en": "Source import and extraction",
    "zh-Hant": "來源導入與提取"
  },
  "预览与可选择对象": {
    "en": "Preview and selectable objects",
    "zh-Hant": "預覽與可選擇對象"
  },
  "作为模板 / 创建字段": {
    "en": "Template / field creation",
    "zh-Hant": "作為模板 / 創建字段"
  },
  "可填充类型": {
    "en": "Fillable types",
    "zh-Hant": "可填充類型"
  },
  "原生导出": {
    "en": "Native export",
    "zh-Hant": "原生導出"
  },
  "PDF 导出": {
    "en": "PDF export",
    "zh-Hant": "PDF 導出"
  },
  "否": {
    "en": "No",
    "zh-Hant": "否"
  },
  "（语言：": {
    "en": " (languages: ",
    "zh-Hant": "（語言："
  },
  "无": {
    "en": "None",
    "zh-Hant": "無"
  },
  "格式保留 / 损失": {
    "en": "Format preservation / loss",
    "zh-Hant": "格式保留 / 損失"
  },
  "处理位置": {
    "en": "Processing location",
    "zh-Hant": "處理位置"
  },
  "批量 / 重试 / 校验": {
    "en": "Batch / retry / verification",
    "zh-Hant": "批量 / 重試 / 校驗"
  },
  "已验证": {
    "en": "Verified",
    "zh-Hant": "已驗證"
  },
  "转换": {
    "en": "Conversion",
    "zh-Hant": "轉換"
  },
  "部分": {
    "en": "Partial",
    "zh-Hant": "部分"
  },
  "缺依赖": {
    "en": "Missing dependency",
    "zh-Hant": "缺依賴"
  },
  "模板已被其他人修改": {
    "en": "Template changed by someone else",
    "zh-Hant": "模板已被其他人修改"
  },
  "你打开的是 v{v1}": {
    "en": "You opened v{v1}",
    "zh-Hant": "你打開的是 v{v1}"
  },
  "你打开这个模板之后": {
    "en": "Since you opened this template",
    "zh-Hant": "你打開這個模板之後"
  },
  "，现在最新的是 v{v1}": {
    "en": "; the latest version is now v{v1}",
    "zh-Hant": "，現在最新的是 v{v1}"
  },
  "，它又被保存过": {
    "en": "; it was saved again",
    "zh-Hant": "，它又被保存過"
  },
  "{v1}{v2}（在其他窗口或由其他人保存）。仍然保存会用你的内容生成一个新版本；对方保存的版本仍然保留，可以在项目中用模板的「撤销」回到它。": {
    "en": "{v1}{v2} (in another window or by someone else). Saving creates a new version using your content. Their version is retained and can be restored with template Undo in a project.",
    "zh-Hant": "{v1}{v2}（在其他窗口或由其他人保存）。仍然保存會用你的內容生成一個新版本；對方保存的版本仍然保留，可以在項目中用模板的「撤銷」回到它。"
  },
  "没有保存：你的修改仍在编辑器中。需要对方的版本时，先复制要保留的内容，再重新打开模板。": {
    "en": "Not saved: your edits remain in the editor. To use the other version, copy any content you want to keep, then reopen the template.",
    "zh-Hant": "沒有保存：你的修改仍在編輯器中。需要對方的版本時，先複製要保留的內容，再重新打開模板。"
  },
  "文档模板": {
    "en": "Document template",
    "zh-Hant": "文檔模板"
  },
  "这个模板不是用文档编辑器创建的，不能在这里编辑。": {
    "en": "This template was not created with the document editor and cannot be edited here.",
    "zh-Hant": "這個模板不是用文檔編輯器創建的，不能在這裡編輯。"
  },
  "放弃未保存的修改？": {
    "en": "Discard unsaved changes?",
    "zh-Hant": "放棄未保存的修改？"
  },
  "模板有尚未保存的修改，离开后这些修改会丢失。": {
    "en": "This template has unsaved changes that will be lost if you leave.",
    "zh-Hant": "模板有尚未保存的修改，離開後這些修改會丟失。"
  },
  "放弃修改并离开": {
    "en": "Discard and leave",
    "zh-Hant": "放棄修改並離開"
  },
  "插入字段": {
    "en": "Insert field",
    "zh-Hant": "插入字段"
  },
  "字段名称（生成时替换为数据）": {
    "en": "Field name (replaced by data during generation)",
    "zh-Hant": "字段名稱（生成時替換為數據）"
  },
  "请先把光标放在表格的某一行中": {
    "en": "Place the cursor in a table row first",
    "zh-Hant": "請先把光標放在表格的某一行中"
  },
  "明细": {
    "en": "Details",
    "zh-Hant": "明細"
  },
  "已设为重复明细行：行内插入的字段即明细列；生成时每条明细复制一行": {
    "en": "Marked as a repeating detail row. Fields inside it become detail columns; each detail produces one row.",
    "zh-Hant": "已設為重復明細行：行內插入的字段即明細列；生成時每條明細複製一行"
  },
  "表格中请使用「设为重复明细行」；重复内容块用于表格以外的段落": {
    "en": "Use “Set as repeating detail row” inside tables. Repeating blocks are for paragraphs outside tables.",
    "zh-Hant": "表格中請使用「設為重復明細行」；重復內容塊用於表格以外的段落"
  },
  "已取消重复内容块（段落保留）": {
    "en": "Repeating block removed; paragraphs retained",
    "zh-Hant": "已取消重復內容塊（段落保留）"
  },
  "无法把选中的内容设为重复内容块（请选中表格以外的连续段落）": {
    "en": "Cannot make this selection a repeating block. Select consecutive paragraphs outside a table.",
    "zh-Hant": "無法把選中的內容設為重復內容塊（請選中表格以外的連續段落）"
  },
  "已设为重复内容块：块内插入的字段就是明细列；生成时每条明细复制一份这些段落": {
    "en": "Marked as a repeating block. Fields inside become detail columns; each detail produces a copy of these paragraphs.",
    "zh-Hant": "已設為重復內容塊：塊內插入的字段就是明細列；生成時每條明細複製一份這些段落"
  },
  "仍然保存": {
    "en": "Save anyway",
    "zh-Hant": "仍然保存"
  },
  "已保存（{v1} 个字段，v{v2}）": {
    "en": "Saved ({v1} fields, v{v2})",
    "zh-Hant": "已保存（{v1} 個字段，v{v2}）"
  },
  "无法打开模板": {
    "en": "Cannot open template",
    "zh-Hant": "無法打開模板"
  },
  "返回": {
    "en": "Back",
    "zh-Hant": "返回"
  },
  "← 返回": {
    "en": "← Back",
    "zh-Hant": "← 返回"
  },
  "模板名称": {
    "en": "Template name",
    "zh-Hant": "模板名稱"
  },
  "文档型模板 → DOCX": {
    "en": "Document template → DOCX",
    "zh-Hant": "文檔型模板 → DOCX"
  },
  "未保存": {
    "en": "Unsaved",
    "zh-Hant": "未保存"
  },
  "已保存": {
    "en": "Saved",
    "zh-Hant": "已保存"
  },
  "保存模板": {
    "en": "Save template",
    "zh-Hant": "保存模板"
  },
  "段落样式": {
    "en": "Paragraph style",
    "zh-Hant": "段落樣式"
  },
  "正文": {
    "en": "Body",
    "zh-Hant": "正文"
  },
  "标题 1": {
    "en": "Heading 1",
    "zh-Hant": "標題 1"
  },
  "标题 2": {
    "en": "Heading 2",
    "zh-Hant": "標題 2"
  },
  "标题 3": {
    "en": "Heading 3",
    "zh-Hant": "標題 3"
  },
  "字体": {
    "en": "Font",
    "zh-Hant": "字體"
  },
  "宋体": {
    "en": "SimSun",
    "zh-Hant": "宋體"
  },
  "黑体": {
    "en": "SimHei",
    "zh-Hant": "黑體"
  },
  "楷体": {
    "en": "KaiTi",
    "zh-Hant": "楷體"
  },
  "仿宋": {
    "en": "FangSong",
    "zh-Hant": "仿宋"
  },
  "微软雅黑": {
    "en": "Microsoft YaHei",
    "zh-Hant": "微軟雅黑"
  },
  "加粗": {
    "en": "Bold",
    "zh-Hant": "加粗"
  },
  "斜体": {
    "en": "Italic",
    "zh-Hant": "斜體"
  },
  "下划线": {
    "en": "Underline",
    "zh-Hant": "下划線"
  },
  "删除线": {
    "en": "Strikethrough",
    "zh-Hant": "刪除線"
  },
  "高亮": {
    "en": "Highlight",
    "zh-Hant": "高亮"
  },
  "两端": {
    "en": "Justify",
    "zh-Hant": "兩端"
  },
  "• 列表": {
    "en": "• Bullet list",
    "zh-Hant": "• 列表"
  },
  "1. 列表": {
    "en": "1. Numbered list",
    "zh-Hant": "1. 列表"
  },
  "＋ 插入字段": {
    "en": "＋ Insert field",
    "zh-Hant": "＋ 插入字段"
  },
  "▦ 表格": {
    "en": "▦ Table",
    "zh-Hant": "▦ 表格"
  },
  "把选中的连续段落设为重复内容块（每条明细复制一份）": {
    "en": "Make selected consecutive paragraphs a repeating block (one copy per detail)",
    "zh-Hant": "把選中的連續段落設為重復內容塊（每條明細複製一份）"
  },
  "✓ 重复内容块": {
    "en": "✓ Repeating block",
    "zh-Hant": "✓ 重復內容塊"
  },
  "⟳ 重复内容块": {
    "en": "⟳ Repeating block",
    "zh-Hant": "⟳ 重復內容塊"
  },
  "🖼 图片": {
    "en": "🖼 Image",
    "zh-Hant": "🖼 圖片"
  },
  "分页符": {
    "en": "Page break",
    "zh-Hant": "分頁符"
  },
  "分隔线": {
    "en": "Horizontal rule",
    "zh-Hant": "分隔線"
  },
  "表格：": {
    "en": "Table: ",
    "zh-Hant": "表格："
  },
  "下方插入行": {
    "en": "Insert row below",
    "zh-Hant": "下方插入行"
  },
  "删除行": {
    "en": "Delete row",
    "zh-Hant": "刪除行"
  },
  "右侧插入列": {
    "en": "Insert column right",
    "zh-Hant": "右側插入列"
  },
  "删除列": {
    "en": "Delete column",
    "zh-Hant": "刪除列"
  },
  "合并单元格": {
    "en": "Merge cells",
    "zh-Hant": "合併單元格"
  },
  "切换表头行": {
    "en": "Toggle header row",
    "zh-Hant": "切換表頭行"
  },
  "单元格底色": {
    "en": "Cell fill",
    "zh-Hant": "單元格底色"
  },
  "删除表格": {
    "en": "Delete table",
    "zh-Hant": "刪除表格"
  },
  "✓ 重复明细行": {
    "en": "✓ Repeating detail row",
    "zh-Hant": "✓ 重復明細行"
  },
  "设为重复明细行": {
    "en": "Set as repeating detail row",
    "zh-Hant": "設為重復明細行"
  },
  "明细名称": {
    "en": "Detail name",
    "zh-Hant": "明細名稱"
  },
  "零条：删除该行": {
    "en": "No details: delete row",
    "zh-Hant": "零條：刪除該行"
  },
  "零条：保留空行": {
    "en": "No details: keep blank row",
    "zh-Hant": "零條：保留空行"
  },
  "零条：显示（无）": {
    "en": "No details: show (none)",
    "zh-Hant": "零條：顯示（無）"
  },
  "零条：删除表格": {
    "en": "No details: delete table",
    "zh-Hant": "零條：刪除表格"
  },
  "行内的字段会成为明细列": {
    "en": "Fields in this row become detail columns",
    "zh-Hant": "行內的字段會成為明細列"
  },
  "内容块名称": {
    "en": "Block name",
    "zh-Hant": "內容塊名稱"
  },
  "相邻：直接相连": {
    "en": "Between copies: no separator",
    "zh-Hant": "相鄰：直接相連"
  },
  "相邻：空一行": {
    "en": "Between copies: blank line",
    "zh-Hant": "相鄰：空一行"
  },
  "相邻：分页": {
    "en": "Between copies: page break",
    "zh-Hant": "相鄰：分頁"
  },
  "零条：删除内容块": {
    "en": "No details: delete block",
    "zh-Hant": "零條：刪除內容塊"
  },
  "零条：视为错误": {
    "en": "No details: report error",
    "zh-Hant": "零條：視為錯誤"
  },
  "块内插入的字段会成为明细列": {
    "en": "Fields inside this block become detail columns",
    "zh-Hant": "塊內插入的字段會成為明細列"
  },
  "纸张": {
    "en": "Paper",
    "zh-Hant": "紙張"
  },
  "纸张大小": {
    "en": "Paper size",
    "zh-Hant": "紙張大小"
  },
  "纸张方向": {
    "en": "Orientation",
    "zh-Hant": "紙張方向"
  },
  "纵向": {
    "en": "Portrait",
    "zh-Hant": "縱向"
  },
  "横向": {
    "en": "Landscape",
    "zh-Hant": "橫向"
  },
  "页边距（毫米）": {
    "en": "Margins (mm)",
    "zh-Hant": "頁邊距（毫米）"
  },
  "页眉": {
    "en": "Header",
    "zh-Hant": "頁眉"
  },
  "页脚": {
    "en": "Footer",
    "zh-Hant": "頁腳"
  },
  "编辑区为近似排版；实际分页以桌面办公软件打开 DOCX 为准。": {
    "en": "The editor approximates layout. Actual pagination is determined by opening the DOCX in a desktop office application.",
    "zh-Hant": "編輯區為近似排版；實際分頁以桌面辦公軟體打開 DOCX 為準。"
  },
  "表格模板": {
    "en": "Spreadsheet template",
    "zh-Hant": "表格模板"
  },
  "这个模板不是用表格编辑器创建的，不能在这里编辑。": {
    "en": "This template was not created with the spreadsheet editor and cannot be edited here.",
    "zh-Hant": "這個模板不是用表格編輯器創建的，不能在這裡編輯。"
  },
  "选区与已有字段重叠": {
    "en": "The selection overlaps an existing field",
    "zh-Hant": "選區與已有字段重疊"
  },
  "字段单元格只能是一个单元格（合并单元格请选左上角）": {
    "en": "A cell field must be a single cell (select the top-left cell of a merged range)",
    "zh-Hant": "字段單元格只能是一個單元格（合併單元格請選左上角）"
  },
  "重复明细行只能选择一行中的连续单元格": {
    "en": "A repeating row must be consecutive cells within one row",
    "zh-Hant": "重復明細行只能選擇一行中的連續單元格"
  },
  "字段单元格": {
    "en": "Cell field",
    "zh-Hant": "字段單元格"
  },
  "表格型模板 → XLSX": {
    "en": "Spreadsheet template → XLSX",
    "zh-Hant": "表格型模板 → XLSX"
  },
  "字": {
    "en": "Text",
    "zh-Hant": "字"
  },
  "底": {
    "en": "Fill",
    "zh-Hant": "底"
  },
  "清底色": {
    "en": "Clear fill",
    "zh-Hant": "清底色"
  },
  "换行": {
    "en": "Wrap",
    "zh-Hant": "換行"
  },
  "边框": {
    "en": "Borders",
    "zh-Hant": "邊框"
  },
  "数字格式": {
    "en": "Number format",
    "zh-Hant": "數字格式"
  },
  "常规": {
    "en": "General",
    "zh-Hant": "常規"
  },
  "yyyy年m月d日": {
    "en": "yyyy年m月d日",
    "zh-Hant": "yyyy年m月d日"
  },
  "合并": {
    "en": "Merge",
    "zh-Hant": "合併"
  },
  "取消合并": {
    "en": "Unmerge",
    "zh-Hant": "取消合併"
  },
  "列宽": {
    "en": "Column width",
    "zh-Hant": "列寬"
  },
  "行高": {
    "en": "Row height",
    "zh-Hant": "行高"
  },
  "+10 行": {
    "en": "+10 rows",
    "zh-Hant": "+10 行"
  },
  "+1 列": {
    "en": "+1 column",
    "zh-Hant": "+1 列"
  },
  "撤销（⌘Z）": {
    "en": "Undo (⌘Z)",
    "zh-Hant": "撤銷（⌘Z）"
  },
  "字段：": {
    "en": "Fields: ",
    "zh-Hant": "字段："
  },
  "设为字段单元格": {
    "en": "Set as cell field",
    "zh-Hant": "設為字段單元格"
  },
  "设为固定区域": {
    "en": "Set as fixed region",
    "zh-Hant": "設為固定區域"
  },
  "表格编辑区，当前 {v1}。方向键移动，Shift+方向键扩展选区，直接输入或 F2 编辑，Tab 离开表格": {
    "en": "Spreadsheet editor, current cell {v1}. Arrows move, Shift+arrows extend, typing or F2 edits, Tab exits the grid",
    "zh-Hant": "表格編輯區，當前 {v1}。方向鍵移動，Shift+方向鍵擴展選區，直接輸入或 F2 編輯，Tab 離開表格"
  },
  "编辑单元格 {v1}": {
    "en": "Edit cell {v1}",
    "zh-Hant": "編輯單元格 {v1}"
  },
  "区域 {v1}": {
    "en": "Region {v1}",
    "zh-Hant": "區域 {v1}"
  },
  "明细行 {v1}": {
    "en": "Detail row {v1}",
    "zh-Hant": "明細行 {v1}"
  },
  "删除字段「{v1}」": {
    "en": "Delete field “{v1}”",
    "zh-Hant": "刪除字段「{v1}」"
  },
  "重命名工作表": {
    "en": "Rename worksheet",
    "zh-Hant": "重命名工作表"
  },
  "删除当前表": {
    "en": "Delete current sheet",
    "zh-Hant": "刪除當前表"
  },
  "双击单元格或直接输入以编辑；双击工作表标签可重命名": {
    "en": "Double-click or type to edit a cell; double-click a sheet tab to rename",
    "zh-Hant": "雙擊單元格或直接輸入以編輯；雙擊工作表標籤可重命名"
  },
  "页面不存在": {
    "en": "Page not found",
    "zh-Hant": "頁面不存在"
  },
  "返回首页": {
    "en": "Back to home",
    "zh-Hant": "返回首頁"
  },
  "该模板是上传的文件，不是内置编辑器创建的：请在项目中直接在模板预览上设置字段。": {
    "en": "This template was uploaded, not created in the built-in editor. Set its fields directly in the template preview within a project.",
    "zh-Hant": "該模板是上傳的文件，不是內置編輯器創建的：請在項目中直接在模板預覽上設置字段。"
  },
  "这个地址在 Synwevo Docs 里不存在，或你没有访问权限。": {
    "en": "This address does not exist in Synwevo Docs, or you do not have access to it.",
    "zh-Hant": "這個地址在 Synwevo Docs 裡不存在，或你沒有存取權限。"
  },
  "切换显示的面板": { "en": "Choose the panel to show", "zh-Hant": "切換顯示的面板" },
  "文件": { "en": "Files", "zh-Hant": "檔案" },
  "目标": { "en": "Target", "zh-Hant": "目標" },
  "预览": { "en": "Preview", "zh-Hant": "預覽" },
  "映射": { "en": "Mappings", "zh-Hant": "映射" },
  "✔ 重复明细：每份输出写入当前分组的全部明细行（按分组与排序规则取行，与所选行数无关）": {
    "en": "✔ Repeating rows: each output receives all detail rows of its group (rows follow the grouping and sort rule, not the selection height)",
    "zh-Hant": "✔ 重複明細：每份輸出寫入當前分組的全部明細行（按分組與排序規則取行，與所選行數無關）"
  },
  "◆ 重复明细：所选明细行整体写入每一份输出（每行一份时，明细区域不随样本变化）": {
    "en": "◆ Repeating rows: the selected detail rows are written whole into every output (with one output per row, the detail block does not vary by sample)",
    "zh-Hant": "◆ 重複明細：所選明細行整體寫入每一份輸出（每行一份時，明細區域不隨樣本變化）"
  },
  "◆ 重复明细：每个来源文件的该明细区域整体写入同一份文档": {
    "en": "◆ Repeating rows: each source file's detail block is written whole into its own document",
    "zh-Hant": "◆ 重複明細：每個來源檔案的該明細區域整體寫入同一份文件"
  },
  "复制到组织工作空间…": {
    "en": "Copy to an organization workspace…",
    "zh-Hant": "複製到組織工作空間…"
  },
  "复制到组织工作空间": {
    "en": "Copy to an organization workspace",
    "zh-Hant": "複製到組織工作空間"
  },
  "已有申请": {
    "en": "Existing requests",
    "zh-Hant": "已有申請"
  },
  "撤回": {
    "en": "Withdraw",
    "zh-Hant": "撤回"
  },
  "切换到目标空间并打开副本": {
    "en": "Switch to the target workspace and open the copy",
    "zh-Hant": "切換到目標空間並開啟副本"
  },
  "你还不是任何组织工作空间的编辑成员。请先让组织把你加入目标空间（编辑成员或以上），再来申请复制。": {
    "en": "You are not yet an editor in any organization workspace. Ask the organization to add you to the target workspace (editor or above), then request the copy.",
    "zh-Hant": "你還不是任何組織工作空間的編輯成員。請先讓組織把你加入目標空間（編輯成員或以上），再來申請複製。"
  },
  "这个项目已有未完成的复制申请；撤回后才能再次申请。": {
    "en": "This project already has an open copy request; withdraw it before asking again.",
    "zh-Hant": "這個專案已有未完成的複製申請；撤回後才能再次申請。"
  },
  "目标工作空间": {
    "en": "Target workspace",
    "zh-Hant": "目標工作空間"
  },
  "请选择…": {
    "en": "Choose…",
    "zh-Hant": "請選擇…"
  },
  "副本名称": {
    "en": "Copy name",
    "zh-Hant": "副本名稱"
  },
  "用途说明（给审批人看，可选）": {
    "en": "Purpose (shown to the approver, optional)",
    "zh-Hant": "用途說明（給審批人看，可選）"
  },
  "复制内容": {
    "en": "What to copy",
    "zh-Hant": "複製內容"
  },
  "项目配置、样本规则、映射与模板（始终复制）": {
    "en": "Project configuration, sample rule, mappings and template (always)",
    "zh-Hant": "專案設定、樣本規則、對應與範本（始終複製）"
  },
  "来源数据文件（{n} 个）": {
    "en": "Source data files ({n})",
    "zh-Hant": "來源資料檔案（{n} 個）"
  },
  "来源文件的修正与识别文字": {
    "en": "Corrections and recognized text of the source files",
    "zh-Hant": "來源檔案的修正與辨識文字"
  },
  "不会复制：任务、产物、上传记录、授权、Webhook、API 凭据。": {
    "en": "Never copied: jobs, artifacts, upload records, grants, webhooks, API credentials.",
    "zh-Hant": "不會複製：任務、產物、上傳記錄、授權、Webhook、API 憑證。"
  },
  "（已固定）": {
    "en": "(pinned)",
    "zh-Hant": "（已固定）"
  },
  "{n} 个字段": {
    "en": "{n} fields",
    "zh-Hant": "{n} 個欄位"
  },
  "修订 {rev}，{n} 条": {
    "en": "Revision {rev}, {n} mappings",
    "zh-Hant": "修訂 {rev}，{n} 條"
  },
  "{n} 个文件，共 {size}": {
    "en": "{n} files, {size} in total",
    "zh-Hant": "{n} 個檔案，共 {size}"
  },
  "我也是目标空间的审批人：批准并复制（记录为自行批准）": {
    "en": "I am also an approver of the target: approve and copy now (recorded as a self-approval)",
    "zh-Hant": "我也是目標空間的審批人：批准並複製（記錄為自行批准）"
  },
  "批准理由": {
    "en": "Reason for approval",
    "zh-Hant": "批准理由"
  },
  "批准并复制": {
    "en": "Approve and copy",
    "zh-Hant": "批准並複製"
  },
  "提交申请": {
    "en": "Submit request",
    "zh-Hant": "送出申請"
  },
  "等待审批": {
    "en": "Awaiting approval",
    "zh-Hant": "等待審批"
  },
  "已批准，复制中": {
    "en": "Approved, copying",
    "zh-Hant": "已批准，複製中"
  },
  "已拒绝": {
    "en": "Rejected",
    "zh-Hant": "已拒絕"
  },
  "已撤回": {
    "en": "Withdrawn",
    "zh-Hant": "已撤回"
  },
  "已过期": {
    "en": "Expired",
    "zh-Hant": "已過期"
  },
  "复制中": {
    "en": "Copying",
    "zh-Hant": "複製中"
  },
  "需重新申请": {
    "en": "Needs a new request",
    "zh-Hant": "需重新申請"
  },
  "从组织共享选择": {
    "en": "From organization shared",
    "zh-Hant": "從組織共享選擇"
  },
  "组织共享模板": {
    "en": "Organization shared templates",
    "zh-Hant": "組織共享範本"
  },
  "发布": {
    "en": "Release",
    "zh-Hant": "發布"
  },
  "维护者": {
    "en": "Maintainer",
    "zh-Hant": "維護者"
  },
  "第 {v1} 次发布 · 模板第 {v2} 版": {
    "en": "Release {v1} · template version {v2}",
    "zh-Hant": "第 {v1} 次發布 · 範本第 {v2} 版"
  },
  "组织共享模板加载失败：": {
    "en": "Shared templates failed to load: ",
    "zh-Hant": "組織共享範本載入失敗："
  },
  "已从组织共享模板复制「{v1}」": {
    "en": "Copied “{v1}” from the organization's shared templates",
    "zh-Hant": "已從組織共享範本複製「{v1}」"
  },
  "已共享 · 第 {v1} 次发布": {
    "en": "Shared · release {v1}",
    "zh-Hant": "已共享 · 第 {v1} 次發布"
  },
  "共享已弃用": {
    "en": "Sharing deprecated",
    "zh-Hant": "共享已棄用"
  },
  "发布到组织…": {
    "en": "Publish to organization…",
    "zh-Hant": "發布到組織…"
  },
  "发布到组织": {
    "en": "Publish to organization",
    "zh-Hant": "發布到組織"
  },
  "已发布到组织": {
    "en": "Published to the organization",
    "zh-Hant": "已發布到組織"
  },
  "更新组织发布": {
    "en": "Release new version",
    "zh-Hant": "更新組織發布"
  },
  "已发布第 {v1} 次": {
    "en": "Release {v1} published",
    "zh-Hant": "已發布第 {v1} 次"
  },
  "弃用共享…": {
    "en": "Deprecate sharing…",
    "zh-Hant": "棄用共享…"
  },
  "弃用共享模板": {
    "en": "Deprecate shared template",
    "zh-Hant": "棄用共享範本"
  },
  "弃用": {
    "en": "Deprecate",
    "zh-Hant": "棄用"
  },
  "已弃用": {
    "en": "Deprecated",
    "zh-Hant": "已棄用"
  },
  "恢复共享": {
    "en": "Restore sharing",
    "zh-Hant": "恢復共享"
  },
  "已恢复共享": {
    "en": "Sharing restored",
    "zh-Hant": "已恢復共享"
  },
  "源模板有新版本，可更新发布": {
    "en": "The source template has a newer version; release it to share",
    "zh-Hant": "來源範本有新版本，可更新發布"
  },
  "模板「{v1}」的共享操作": {
    "en": "Sharing actions for template “{v1}”",
    "zh-Hant": "範本「{v1}」的共享操作"
  },
  "定位到字段": {
    "en": "Go to field",
    "zh-Hant": "定位到欄位"
  },
  "定位到映射": {
    "en": "Go to mapping",
    "zh-Hant": "定位到對應"
  },
  "定位到文件": {
    "en": "Go to file",
    "zh-Hant": "定位到檔案"
  },
  "链接指向的{v1}已不存在": {
    "en": "The {v1} this link points to no longer exists",
    "zh-Hant": "連結指向的{v1}已不存在"
  },
  "版本": {
    "en": "Versions",
    "zh-Hant": "版本"
  },
  "版本历史与恢复": {
    "en": "Version history and restore",
    "zh-Hant": "版本歷史與恢復"
  },
  "模板版本": {
    "en": "Template versions",
    "zh-Hant": "範本版本"
  },
  "模板版本历史…": {
    "en": "Template version history…",
    "zh-Hant": "範本版本歷史…"
  },
  "使用此模板的项目：{v1} 个跟随当前版本，{v2} 个固定了版本（恢复后不受影响）": {
    "en": "Projects using this template: {v1} follow the current version, {v2} are pinned to a version (unaffected by a restore)",
    "zh-Hant": "使用此範本的專案：{v1} 個跟隨目前版本，{v2} 個固定了版本（恢復後不受影響）"
  },
  "时间": {
    "en": "Time",
    "zh-Hant": "時間"
  },
  "确认恢复": {
    "en": "Confirm restore",
    "zh-Hant": "確認恢復"
  },
  "恢复此版本": {
    "en": "Restore this version",
    "zh-Hant": "恢復此版本"
  },
  "将以第 {v1} 版的内容新建第 {v2} 版并成为当前版本；跟随当前版本的项目会随之变化，固定了版本的项目不变。": {
    "en": "Version {v2} will be created from version {v1}'s content and become current; projects following the current version move with it, pinned projects do not.",
    "zh-Hant": "將以第 {v1} 版的內容新建第 {v2} 版並成為目前版本；跟隨目前版本的專案會隨之變化，固定了版本的專案不變。"
  },
  "已把第 {v1} 版的内容恢复为第 {v2} 版（当前）": {
    "en": "Version {v1}'s content is now version {v2} (current)",
    "zh-Hant": "已把第 {v1} 版的內容恢復為第 {v2} 版（目前）"
  },
  "{v1} 分钟前": {
    "en": "{v1} min ago",
    "zh-Hant": "{v1} 分鐘前"
  },
  "{v1} 小时前": {
    "en": "{v1} h ago",
    "zh-Hant": "{v1} 小時前"
  },
  "{v1} 已{v2}模板版本（现为 v{v3}），编辑内容已随之更新": {
    "en": "{v1} {v2} a template version (now v{v3}); the editor content followed",
    "zh-Hant": "{v1} 已{v2}範本版本（現為 v{v3}），編輯內容已隨之更新"
  },
  "{v1} 已把当前内容保存为 v{v2}": {
    "en": "{v1} saved the current content as v{v2}",
    "zh-Hant": "{v1} 已把目前內容儲存為 v{v2}"
  },
  "{v1}（查看）": {
    "en": "{v1} (viewing)",
    "zh-Hant": "{v1}（檢視）"
  },
  "与协同服务的连接已断开，正在重试；你的修改会在重新连接后同步。": {
    "en": "The connection to the collaboration service dropped; retrying. Your changes sync once it is back.",
    "zh-Hant": "與協同服務的連線已中斷，正在重試；你的修改會在重新連線後同步。"
  },
  "你现在只能查看：你在该工作空间的角色是查看者，修改不会被接受。": {
    "en": "You can only view: your role in this workspace is viewer, so changes are not accepted.",
    "zh-Hant": "你現在只能檢視：你在該工作空間的角色是檢視者，修改不會被接受。"
  },
  "你现在只能查看：刚才的修改没有被接受。你在该工作空间的角色已变为查看者。": {
    "en": "You can only view now: your last change was not accepted. Your role in this workspace changed to viewer.",
    "zh-Hant": "你現在只能檢視：剛才的修改沒有被接受。你在該工作空間的角色已變為檢視者。"
  },
  "修改了{v1}第 {v2} 个{v3}": {
    "en": "edited {v3} {v2} of the {v1}",
    "zh-Hant": "修改了{v1}第 {v2} 個{v3}"
  },
  "修改已实时同步给协作者并保留为草稿，但还没有保存为新版本；生成文档时使用的是已保存的版本。": {
    "en": "Your changes are synced to collaborators and kept as a draft, but not saved as a new version yet; documents are generated from the saved version.",
    "zh-Hant": "修改已即時同步給協作者並保留為草稿，但還沒有儲存為新版本；產生文件時使用的是已儲存的版本。"
  },
  "内容": {
    "en": "content",
    "zh-Hant": "內容"
  },
  "列表": {
    "en": "list",
    "zh-Hant": "清單"
  },
  "刚刚": {
    "en": "just now",
    "zh-Hant": "剛剛"
  },
  "删除了{v1}第 {v2} 个{v3}": {
    "en": "deleted {v3} {v2} of the {v1}",
    "zh-Hant": "刪除了{v1}第 {v2} 個{v3}"
  },
  "单人编辑": {
    "en": "Editing alone",
    "zh-Hant": "單人編輯"
  },
  "变更记录": {
    "en": "Change log",
    "zh-Hant": "變更記錄"
  },
  "同步中…": {
    "en": "Syncing…",
    "zh-Hant": "同步中…"
  },
  "在{v1}第 {v2} 处新增了{v3}": {
    "en": "inserted a {v3} at position {v2} of the {v1}",
    "zh-Hant": "在{v1}第 {v2} 處新增了{v3}"
  },
  "在线成员": {
    "en": "People here",
    "zh-Hant": "線上成員"
  },
  "在编辑器之外保存了 {v1}（编辑内容随之更新）": {
    "en": "saved {v1} outside the editor (the editor content followed)",
    "zh-Hant": "在編輯器之外儲存了 {v1}（編輯內容隨之更新）"
  },
  "在编辑器之外保存了 {v1}（替换了 {v2}，编辑内容随之更新）": {
    "en": "saved {v1} outside the editor, replacing {v2} (the editor content followed)",
    "zh-Hant": "在編輯器之外儲存了 {v1}（替換了 {v2}，編輯內容隨之更新）"
  },
  "把模板从 {v2} 恢复为 {v1} 的内容（编辑内容随之更新）": {
    "en": "restored the template from {v2} to the content of {v1} (the editor content followed)",
    "zh-Hant": "把範本從 {v2} 復原為 {v1} 的內容（編輯內容隨之更新）"
  },
  "实时协同不可用（{v1}）。": {
    "en": "Live collaboration is unavailable ({v1}).",
    "zh-Hant": "即時協同不可用（{v1}）。"
  },
  "实时协同不可用：你在该工作空间的权限不允许加入编辑。": {
    "en": "Live collaboration is unavailable: your permissions in this workspace do not allow joining the editing session.",
    "zh-Hant": "即時協同不可用：你在該工作空間的權限不允許加入編輯。"
  },
  "实时协同不可用：模板不存在或已删除。": {
    "en": "Live collaboration is unavailable: the template does not exist or was deleted.",
    "zh-Hant": "即時協同不可用：範本不存在或已刪除。"
  },
  "实时协同中": {
    "en": "Live",
    "zh-Hant": "即時協同中"
  },
  "引用": {
    "en": "quote",
    "zh-Hant": "引用"
  },
  "恢复": {
    "en": "restored",
    "zh-Hant": "復原"
  },
  "成员": {
    "en": "A member",
    "zh-Hant": "成員"
  },
  "我": {
    "en": "Me",
    "zh-Hant": "我"
  },
  "把模板恢复为 {v1}（编辑内容随之更新）": {
    "en": "restored the template to {v1} (the editor content followed)",
    "zh-Hant": "把範本復原為 {v1}（編輯內容隨之更新）"
  },
  "按内容块记录谁改了什么；保存模板会把当前内容记为新版本。": {
    "en": "Who changed what, block by block; saving the template records the current content as a new version.",
    "zh-Hant": "按內容區塊記錄誰改了什麼；儲存範本會把目前內容記為新版本。"
  },
  "撤销（只撤销我自己的修改）": {
    "en": "Undo (only my own changes)",
    "zh-Hant": "復原（只復原我自己的修改）"
  },
  "无法连接实时协同：正在单人编辑已保存的版本；其他人的修改不会显示，保存时若版本已被更新会先询问。": {
    "en": "Cannot reach live collaboration: editing the saved version alone. Others' changes are not shown; if the version moved on, saving asks first.",
    "zh-Hant": "無法連線即時協同：正在單人編輯已儲存的版本；其他人的修改不會顯示，儲存時若版本已被更新會先詢問。"
  },
  "更换": {
    "en": "replaced",
    "zh-Hant": "更換"
  },
  "有未保存为版本的修改": {
    "en": "Changes not saved as a version",
    "zh-Hant": "有未儲存為版本的修改"
  },
  "标题": {
    "en": "heading",
    "zh-Hant": "標題"
  },
  "段落": {
    "en": "paragraph",
    "zh-Hant": "段落"
  },
  "登录已失效：实时协同已停止，请重新登录后刷新。": {
    "en": "Your sign-in ended: live collaboration stopped. Sign in again and reload.",
    "zh-Hant": "登入已失效：即時協同已停止，請重新登入後重新整理。"
  },
  "离开": {
    "en": "Leave",
    "zh-Hant": "離開"
  },
  "离开编辑器？": {
    "en": "Leave the editor?",
    "zh-Hant": "離開編輯器？"
  },
  "系统": {
    "en": "System",
    "zh-Hant": "系統"
  },
  "表格": {
    "en": "table",
    "zh-Hant": "表格"
  },
  "还没有协同修改记录。": {
    "en": "No collaborative changes recorded yet.",
    "zh-Hant": "還沒有協同修改記錄。"
  },
  "连接中…": {
    "en": "Connecting…",
    "zh-Hant": "連線中…"
  },
  "连接已断开，重试中": {
    "en": "Disconnected, retrying",
    "zh-Hant": "連線已中斷，重試中"
  },
  "重新连接": {
    "en": "Reconnect",
    "zh-Hant": "重新連線"
  },
  "（{v1} 次）": {
    "en": " ({v1} times)",
    "zh-Hant": "（{v1} 次）"
  },
  "（查看）": {
    "en": " (viewing)",
    "zh-Hant": "（檢視）"
  },
  "{v1} {v2}": {
    "en": "{v1} {v2}",
    "zh-Hant": "{v1} {v2}"
  },
  "{v1} 正在看{v2}（点击定位）": {
    "en": "{v1} is on {v2} (click to go there)",
    "zh-Hant": "{v1} 正在看{v2}（點擊定位）"
  },
  "{v1}（第 {v2} 版）": {
    "en": "{v1} (revision {v2})",
    "zh-Hant": "{v1}（第 {v2} 版）"
  },
  "{v1}（第 {v2} 版，与第 {v3} 版自动合并）": {
    "en": "{v1} (revision {v2}, merged with revision {v3})",
    "zh-Hant": "{v1}（第 {v2} 版，與第 {v3} 版自動合併）"
  },
  "以我的为准": {
    "en": "Keep mine",
    "zh-Hant": "以我的為準"
  },
  "你删除了它，对方改了它": {
    "en": "you deleted it, they changed it",
    "zh-Hant": "你刪除了它，對方改了它"
  },
  "保存了映射": {
    "en": "saved the mappings",
    "zh-Hant": "儲存了對應"
  },
  "修改了文件读取选项": {
    "en": "changed a file's reading options",
    "zh-Hant": "修改了檔案讀取選項"
  },
  "修改了项目": {
    "en": "changed the project",
    "zh-Hant": "修改了專案"
  },
  "修改了项目设置（{v1}）": {
    "en": "changed project settings ({v1})",
    "zh-Hant": "修改了專案設定（{v1}）"
  },
  "修改映射 {v1}": {
    "en": "changed mapping {v1}",
    "zh-Hant": "修改對應 {v1}"
  },
  "其他成员在你之后也保存了映射。没有冲突的修改已可合并；下面这些映射双方都动过，请选择保留哪一方：{v1}": {
    "en": "Another member saved the mappings after you loaded them. Changes that do not collide merge; both of you changed the mappings below, so choose whose version to keep: {v1}",
    "zh-Hant": "其他成員在你之後也儲存了對應。沒有衝突的修改已可合併；下面這些對應雙方都動過，請選擇保留哪一方：{v1}"
  },
  "删除映射 {v1}": {
    "en": "removed mapping {v1}",
    "zh-Hant": "刪除對應 {v1}"
  },
  "双方各自新增": {
    "en": "both sides added it",
    "zh-Hant": "雙方各自新增"
  },
  "双方都改了它": {
    "en": "both of you changed it",
    "zh-Hant": "雙方都改了它"
  },
  "实时更新不可用": {
    "en": "Live updates unavailable",
    "zh-Hant": "即時更新不可用"
  },
  "实时更新中": {
    "en": "Live",
    "zh-Hant": "即時更新中"
  },
  "对方已删除它": {
    "en": "they deleted it",
    "zh-Hant": "對方已刪除它"
  },
  "已改为对方的版本；你对这些映射的修改未保存": {
    "en": "Switched to their version; your changes to those mappings were not saved",
    "zh-Hant": "已改為對方的版本；你對這些對應的修改未儲存"
  },
  "恢复了模板版本（现为第 {v1} 版）": {
    "en": "restored a template version (now v{v1})",
    "zh-Hant": "復原了範本版本（現為第 {v1} 版）"
  },
  "撤销了映射修改（回到第 {v1} 版）": {
    "en": "undid a mapping change (back to revision {v1})",
    "zh-Hant": "復原了對應修改（回到第 {v1} 版）"
  },
  "撤销了模板修改（现为第 {v1} 版）": {
    "en": "undid a template change (now v{v1})",
    "zh-Hant": "復原了範本修改（現為第 {v1} 版）"
  },
  "新增映射 {v1}": {
    "en": "added mapping {v1}",
    "zh-Hant": "新增對應 {v1}"
  },
  "映射 #{v1}：{v2}": {
    "en": "mapping #{v1}: {v2}",
    "zh-Hant": "對應 #{v1}：{v2}"
  },
  "映射有冲突": {
    "en": "Mapping conflict",
    "zh-Hant": "對應有衝突"
  },
  "更换了代表文件": {
    "en": "changed the representative file",
    "zh-Hant": "更換了代表檔案"
  },
  "更新了模板（第 {v1} 版）": {
    "en": "updated the template (v{v1})",
    "zh-Hant": "更新了範本（第 {v1} 版）"
  },
  "此页面收不到其他人的实时修改；刷新页面重试。": {
    "en": "This page cannot receive others' live changes; reload to retry.",
    "zh-Hant": "此頁面收不到其他人的即時修改；重新整理頁面重試。"
  },
  "活动记录": {
    "en": "Activity",
    "zh-Hant": "活動記錄"
  },
  "活动记录…": {
    "en": "Activity…",
    "zh-Hant": "活動記錄…"
  },
  "添加了文件「{v1}」": {
    "en": "added the file “{v1}”",
    "zh-Hant": "新增了檔案「{v1}」"
  },
  "移除了文件「{v1}」": {
    "en": "removed the file “{v1}”",
    "zh-Hant": "移除了檔案「{v1}」"
  },
  "调整了文件顺序": {
    "en": "reordered the files",
    "zh-Hant": "調整了檔案順序"
  },
  "输出设置": {
    "en": "output settings",
    "zh-Hant": "輸出設定"
  },
  "还没有记录。": {
    "en": "No activity yet.",
    "zh-Hant": "還沒有記錄。"
  },
  "重做了映射修改（第 {v1} 版）": {
    "en": "redid a mapping change (revision {v1})",
    "zh-Hant": "重做了對應修改（第 {v1} 版）"
  },
  "重做了模板修改（现为第 {v1} 版）": {
    "en": "redid a template change (now v{v1})",
    "zh-Hant": "重做了範本修改（現為第 {v1} 版）"
  },
  "，启用": {
    "en": ", enabled",
    "zh-Hant": "，啟用"
  },
  "，禁用": {
    "en": ", disabled",
    "zh-Hant": "，停用"
  },
  "{v1} 已{v2}第 {v3} 版，已载入": {
    "en": "{v1} {v2} version {v3}; it is loaded",
    "zh-Hant": "{v1} 已{v2}第 {v3} 版，已載入"
  },
  "{v1} 已{v2}第 {v3} 版；你的修改会在保存时按单元格与之合并": {
    "en": "{v1} {v2} version {v3}; your changes merge with it cell by cell when you save",
    "zh-Hant": "{v1} 已{v2}第 {v3} 版；你的修改會在儲存時按儲存格與之合併"
  },
  "{v1} 的 {v2}": {
    "en": "{v2} on {v1}",
    "zh-Hant": "{v1} 的 {v2}"
  },
  "保存": {
    "en": "saved",
    "zh-Hant": "儲存"
  },
  "其他成员在你之后也保存了这个模板。没有冲突的单元格已可合并；下面这些双方都动过，请选择保留哪一方：{v1}": {
    "en": "Another member saved this template after you loaded it. Cells that do not collide merge; both of you changed the items below, so choose whose version to keep: {v1}",
    "zh-Hant": "其他成員在你之後也儲存了這個範本。沒有衝突的儲存格已可合併；下面這些雙方都動過，請選擇保留哪一方：{v1}"
  },
  "内容与已保存的第 {v1} 版相同，无需新版本": {
    "en": "Same content as saved version {v1}; no new version needed",
    "zh-Hant": "內容與已儲存的第 {v1} 版相同，無需新版本"
  },
  "单元格 {v1}（{v2}）": {
    "en": "cell {v1} ({v2})",
    "zh-Hant": "儲存格 {v1}（{v2}）"
  },
  "工作表「{v1}」": {
    "en": "sheet “{v1}”",
    "zh-Hant": "工作表「{v1}」"
  },
  "工作表「{v1}」的行列与合并": {
    "en": "rows, columns and merges of sheet “{v1}”",
    "zh-Hant": "工作表「{v1}」的行列與合併"
  },
  "已保存并与第 {v1} 版合并（{v2} 个字段，v{v3}）": {
    "en": "Saved and merged with version {v1} ({v2} fields, v{v3})",
    "zh-Hant": "已儲存並與第 {v1} 版合併（{v2} 個欄位，v{v3}）"
  },
  "没有保存：已改为对方的版本，你的修改仍在编辑器中": {
    "en": "Not saved: switched to their version; your changes are still in the editor",
    "zh-Hant": "沒有儲存：已改為對方的版本，你的修改仍在編輯器中"
  },
  "表格有冲突": {
    "en": "Table conflict",
    "zh-Hant": "表格有衝突"
  },
  "编辑器里有尚未保存为版本的实时修改（在线：{v1}）。恢复会丢弃这些修改。": {
    "en": "The editor holds live edits not yet saved as a version (online: {v1}). Restoring discards them.",
    "zh-Hant": "編輯器裡有尚未儲存為版本的即時修改（線上：{v1}）。還原會捨棄這些修改。"
  },
  "编辑器里有尚未保存为版本的实时修改（最后由 {v1} 修改）。恢复会丢弃这些修改。": {
    "en": "The editor holds live edits not yet saved as a version (last changed by {v1}). Restoring discards them.",
    "zh-Hant": "編輯器裡有尚未儲存為版本的即時修改（最後由 {v1} 修改）。還原會捨棄這些修改。"
  },
  "编辑器里有尚未保存为版本的实时修改。恢复会丢弃这些修改。": {
    "en": "The editor holds live edits not yet saved as a version. Restoring discards them.",
    "zh-Hant": "編輯器裡有尚未儲存為版本的即時修改。還原會捨棄這些修改。"
  },
  "确认恢复并丢弃实时修改": {
    "en": "Restore and discard live edits",
    "zh-Hant": "確認還原並捨棄即時修改"
  },
  "有人刚刚修改了编辑器内容，这次没有恢复；请看清将丢弃的内容后再确认。": {
    "en": "Someone just changed the editor's content, so nothing was restored; check what would be discarded, then confirm.",
    "zh-Hant": "有人剛剛修改了編輯器內容，這次沒有還原；請看清將捨棄的內容後再確認。"
  },
  "编辑器里有尚未保存为版本的实时修改（{v1}），这一步没有执行。要放弃这些修改，请在模板版本历史里恢复版本。": {
    "en": "The editor holds live edits not yet saved as a version ({v1}); this step was not applied. To drop those edits, restore a version from the template history.",
    "zh-Hant": "編輯器裡有尚未儲存為版本的即時修改（{v1}），這一步沒有執行。要放棄這些修改，請在範本版本歷史裡還原版本。"
  },
  "编辑器里有尚未保存为版本的实时修改，这一步没有执行。要放弃这些修改，请在模板版本历史里恢复版本。": {
    "en": "The editor holds live edits not yet saved as a version; this step was not applied. To drop those edits, restore a version from the template history.",
    "zh-Hant": "編輯器裡有尚未儲存為版本的即時修改，這一步沒有執行。要放棄這些修改，請在範本版本歷史裡還原版本。"
  },
  "添加了评论": {
    "en": "added a comment",
    "zh-Hant": "新增了留言"
  },
  "回复了评论": {
    "en": "replied to a comment",
    "zh-Hant": "回覆了留言"
  },
  "修改了评论": {
    "en": "edited a comment",
    "zh-Hant": "修改了留言"
  },
  "把评论标记为已解决": {
    "en": "marked a comment resolved",
    "zh-Hant": "把留言標記為已解決"
  },
  "重新打开了评论": {
    "en": "reopened a comment",
    "zh-Hant": "重新開啟了留言"
  },
  "删除了评论": {
    "en": "deleted a comment",
    "zh-Hant": "刪除了留言"
  },
  "项目设置": {
    "en": "Project settings",
    "zh-Hant": "專案設定"
  },
  "全部成员": {
    "en": "Everyone",
    "zh-Hant": "全部成員"
  },
  "按成员筛选": {
    "en": "Filter by member",
    "zh-Hant": "按成員篩選"
  },
  "按对象筛选": {
    "en": "Filter by object",
    "zh-Hant": "按物件篩選"
  },
  "上次查看后 {v1} 条": {
    "en": "{v1} since you last looked",
    "zh-Hant": "上次查看後 {v1} 條"
  },
  "没有符合筛选的记录。": {
    "en": "Nothing matches the filter.",
    "zh-Hant": "沒有符合篩選的記錄。"
  },
  "跳转": {
    "en": "jump",
    "zh-Hant": "跳轉"
  },
  "以上是上次查看后的更改": {
    "en": "Above: changes since you last looked",
    "zh-Hant": "以上是上次查看後的更改"
  },
  "跳到 {v1} 改的对象": {
    "en": "Jump to what {v1} changed",
    "zh-Hant": "跳到 {v1} 改的物件"
  },
  "版本说明（可选）": {
    "en": "Version note (optional)",
    "zh-Hant": "版本說明（選填）"
  },
  "这次保存的版本说明，可留空": {
    "en": "A note for the version this save creates; may be left empty",
    "zh-Hant": "這次儲存的版本說明，可留空"
  },
  "新增的内容": {
    "en": "Inserted",
    "zh-Hant": "新增的內容"
  },
  "删除的内容": {
    "en": "Deleted",
    "zh-Hant": "刪除的內容"
  },
  "尚未保存为版本的实时修改（{v1} 条）": {
    "en": "Live edits not yet saved as a version ({v1})",
    "zh-Hant": "尚未儲存為版本的即時修改（{v1} 條）"
  },
  "这次发送的内容超过实时协同允许的大小，连接已关闭（{v1}）": {
    "en": "What was sent exceeds the size live co-editing allows; the connection was closed ({v1})",
    "zh-Hant": "這次傳送的內容超過即時協同允許的大小，連線已關閉（{v1}）"
  },
  "模板已被删除（{v1}）": {
    "en": "The template was deleted ({v1})",
    "zh-Hant": "範本已被刪除（{v1}）"
  },
  "这个模板不是内置文档模板，不能实时协同（{v1}）": {
    "en": "This template is not a built-in document template, so it cannot be co-edited live ({v1})",
    "zh-Hant": "這個範本不是內建文件範本，不能即時協同（{v1}）"
  },
  "服务内部出错（{v1}）": {
    "en": "An internal server error occurred ({v1})",
    "zh-Hant": "服務內部出錯（{v1}）"
  },
  "{v1}（{v2}）": {
    "en": "{v1} ({v2})",
    "zh-Hant": "{v1}（{v2}）"
  },
  "这个模板之前未保存的实时修改无法读取，已改用第 {v1} 版的内容；请检查后再继续。": {
    "en": "This template's earlier unsaved live edits could not be read; version {v1} is in use instead. Check the content before continuing.",
    "zh-Hant": "這個範本之前未儲存的即時修改無法讀取，已改用第 {v1} 版的內容；請檢查後再繼續。"
  },
  "另一个模板": {
    "en": "another template",
    "zh-Hant": "另一個範本"
  },
  "固定第 {v1} 版": {
    "en": "pinned to v{v1}",
    "zh-Hant": "固定第 {v1} 版"
  },
  "（工作表「{v1}」）": {
    "en": "(sheet “{v1}”)",
    "zh-Hant": "（工作表「{v1}」）"
  },
  "（按「{v1}」）": {
    "en": "(by “{v1}”)",
    "zh-Hant": "（按「{v1}」）"
  },
  "（标记「{v1}」）": {
    "en": "(marker “{v1}”)",
    "zh-Hant": "（標記「{v1}」）"
  },
  "出错跳过": {
    "en": "skip on error",
    "zh-Hant": "出錯跳過"
  },
  "出错阻止": {
    "en": "stop on error",
    "zh-Hant": "出錯阻止"
  },
  "自动缩放": {
    "en": "fit to page",
    "zh-Hant": "自動縮放"
  },
  "溢出提醒": {
    "en": "warn on overflow",
    "zh-Hant": "溢出提醒"
  },
  "命名「{v1}」": {
    "en": "named “{v1}”",
    "zh-Hant": "命名「{v1}」"
  },
  "默认": {
    "en": "default",
    "zh-Hant": "預設"
  },
  "此部署未启用账户，这个功能需要登录的成员。": {
    "en": "This deployment has no accounts; this feature needs a signed-in member.",
    "zh-Hant": "此部署未啟用帳戶，這個功能需要登入的成員。"
  },
  "删除项目「{v1}」？只想让它从列表里消失时，用「归档」更合适，随时可以撤销。": {
    "en": "Delete project “{v1}”? If you only want it out of the list, archiving is the better choice and can be undone at any time.",
    "zh-Hant": "刪除項目「{v1}」？只想讓它從列表裡消失時，用「歸檔」更合適，隨時可以撤銷。"
  },
  "一个项目把一批来源文件映射到一个模板并批量生成文档；同结构的新文件可以直接复用。": {
    "en": "A project maps a batch of source files onto one template and generates documents in bulk; new files with the same structure reuse it as is.",
    "zh-Hant": "一個項目把一批來源文件映射到一個模板並批量生成文檔；同結構的新文件可以直接複用。"
  },
  "还没有模板。在项目中上传或新建的模板会出现在这里。": {
    "en": "No templates yet. Templates uploaded or created in a project appear here.",
    "zh-Hant": "還沒有模板。在項目中上傳或新建的模板會出現在這裡。"
  },
  "来源工作空间": {
    "en": "Source workspace",
    "zh-Hant": "來源工作空間"
  },
  "已用于 {v1} 个工作空间": {
    "en": "Used in {v1} workspaces",
    "zh-Hant": "已用於 {v1} 個工作空間"
  },
  "复制到本工作空间": {
    "en": "Copy into this workspace",
    "zh-Hant": "複製到本工作空間"
  },
  "组织里还没有共享模板。在模板库对模板选择「发布到组织…」。": {
    "en": "The organization has no shared templates yet. Choose “Publish to organization…” on a template in the library.",
    "zh-Hant": "組織裡還沒有共享模板。在模板庫對模板選擇「發布到組織…」。"
  },
  "所有文件都在本服务的服务器上处理，不发送到外部服务。": {
    "en": "All files are processed on this service's own server and never sent to external services.",
    "zh-Hant": "所有文件都在本服務的伺服器上處理，不發送到外部服務。"
  },
  "弃用后组织成员不能再用它建立新项目；已复制到各工作空间的模板与任务不受影响。": {
    "en": "Once deprecated, members can no longer use it for new projects; copies already made in workspaces and their jobs are unaffected.",
    "zh-Hant": "棄用後組織成員不能再用它建立新項目；已複製到各工作空間的模板與任務不受影響。"
  },
  "识别扫描页上的文字（中英文），在本服务器完成": {
    "en": "Recognize text on scanned pages (Chinese and English); runs on this server",
    "zh-Hant": "辨識掃描頁上的文字（中英文），在本伺服器完成"
  },
  "先在来源文件列表中上传文件。": {
    "en": "Upload files in the source file list first.",
    "zh-Hant": "先在來源文件列表中上傳文件。"
  },
  "还没有设置模板": {
    "en": "No template set yet",
    "zh-Hant": "還沒有設置模板"
  },
  "自动识别：编码 {v1}，分隔符「{v2}」。出现乱码或分列错误时，请手动指定。": {
    "en": "Detected: encoding {v1}, delimiter “{v2}”. If text is garbled or columns split wrongly, set them manually.",
    "zh-Hant": "自動識別：編碼 {v1}，分隔符「{v2}」。出現亂碼或分列錯誤時，請手動指定。"
  },
  "选中左侧的数据和右侧的字段后，点「建立映射」。": {
    "en": "Select data on the left and a field on the right, then choose “Create mapping”.",
    "zh-Hant": "選中左側的數據和右側的字段後，點「建立映射」。"
  },
  "值为空时的默认值（可选）": {
    "en": "Default when the value is empty (optional)",
    "zh-Hant": "值為空時的預設值（可選）"
  },
  "每个来源文件都按代表文件上建立的映射逐一核对；无法定位的映射会标出。": {
    "en": "Every source file is checked against the mappings made on the representative file; mappings that cannot be located are flagged.",
    "zh-Hant": "每個來源文件都按代表文件上建立的映射逐一核對；無法定位的映射會標出。"
  },
  "原始文件保持不变。上传后在右侧预览中选中内容，用「设为待填区域」创建字段。": {
    "en": "The original file is never changed. After uploading, select content in the preview on the right and use “Set as fillable region” to create fields.",
    "zh-Hant": "原始文件保持不變。上傳後在右側預覽中選中內容，用「設為待填區域」創建字段。"
  },
  "段落、表格、图片、页眉页脚与页面设置；在任意位置插入字段。": {
    "en": "Paragraphs, tables, images, headers and footers, page setup; insert fields anywhere.",
    "zh-Hant": "段落、表格、圖片、頁眉頁腳與頁面設置；在任意位置插入字段。"
  },
  "单元格样式、合并与数字格式；定义待填单元格、固定区域和重复明细行。": {
    "en": "Cell styles, merges and number formats; define fillable cells, fixed regions and repeating detail rows.",
    "zh-Hant": "單元格樣式、合併與數字格式；定義待填單元格、固定區域和重複明細行。"
  },
  "模板可被多个项目共用，修改对它们都生效；不想影响其他项目时选「复制后使用」。": {
    "en": "A template can be shared by several projects and changes affect all of them; choose “Duplicate and use” to leave other projects unaffected.",
    "zh-Hant": "模板可被多個項目共用，修改對它們都生效；不想影響其他項目時選「複製後使用」。"
  },
  "复制到本工作空间并用于这个项目": {
    "en": "Copy into this workspace and use it for this project",
    "zh-Hant": "複製到本工作空間並用於這個項目"
  },
  "会复制为本工作空间自己的模板；组织之后更新或弃用它，不影响这里的项目和任务。": {
    "en": "A copy becomes this workspace's own template; later updates or deprecation by the organization do not affect the projects and jobs here.",
    "zh-Hant": "會複製為本工作空間自己的模板；組織之後更新或棄用它，不影響這裡的項目和任務。"
  },
  "预计产物": {
    "en": "Expected artifacts",
    "zh-Hant": "預計產物"
  },
  "公式会在生成时计算并写入结果；无法计算的公式会在结果清单中注明。": {
    "en": "Formulas are calculated at generation and their results written in; any that cannot be calculated are noted in the result list.",
    "zh-Hant": "公式會在生成時計算並寫入結果；無法計算的公式會在結果清單中註明。"
  },
  "逐份排版，页数多于模板时提示": {
    "en": "Lay out each copy; warn when it has more pages than the template",
    "zh-Hant": "逐份排版，頁數多於模板時提示"
  },
  "每份多花约 1 秒；页数超出的样本会在结果清单中标出。": {
    "en": "About one extra second per copy; samples that run over are flagged in the result list.",
    "zh-Hant": "每份多花約 1 秒；頁數超出的樣本會在結果清單中標出。"
  },
  "只缩小勾选了「超长时允许缩小字号」的字段。": {
    "en": "Only fields with “Allow smaller text when too long” are shrunk.",
    "zh-Hant": "只縮小勾選了「超長時允許縮小字號」的字段。"
  },
  "任务使用开始时的文件、模板版本、映射与样本规则；之后的修改不影响本任务。": {
    "en": "The job uses the files, template version, mappings and sample rule as they were when it started; later changes do not affect it.",
    "zh-Hant": "任務使用開始時的文件、模板版本、映射與樣本規則；之後的修改不影響本任務。"
  },
  "模板中的 TODAY() / NOW() 按 {v1} 时间 {v2} 计算。": {
    "en": "TODAY() and NOW() in the template were computed at {v2}, {v1} time.",
    "zh-Hant": "範本中的 TODAY() / NOW() 按 {v1} 時間 {v2} 計算。"
  },
  "产物": {
    "en": "Artifacts",
    "zh-Hant": "產物"
  },
  "还没有产物": {
    "en": "No artifacts yet",
    "zh-Hant": "還沒有產物"
  },
  "样本与产物": {
    "en": "Samples and artifacts",
    "zh-Hant": "樣本與產物"
  },
  "样本只决定哪些数据构成一份输出；多个来源文件各自提取样本后按文件顺序追加。": {
    "en": "Samples only decide which data makes up one output; each source file yields its own samples, appended in file order.",
    "zh-Hant": "樣本只決定哪些數據構成一份輸出；多個來源文件各自提取樣本後按文件順序追加。"
  },
  "从每个标记出现的位置开始一个块，到下一个标记（或结束标记）为止；同一页可有多块，块也可跨页。建立映射时，用左侧的块导航切换到某一块再选取数据。": {
    "en": "A block starts at each occurrence of the marker and ends at the next marker (or the end marker); a page can hold several blocks and a block can span pages. When mapping, switch to a block with the block navigation on the left, then select the data.",
    "zh-Hant": "從每個標記出現的位置開始一個塊，到下一個標記（或結束標記）為止；同一頁可有多塊，塊也可跨頁。建立映射時，用左側的塊導航切換到某一塊再選取數據。"
  },
  " 排除完全空白的行": {
    "en": " Skip completely blank rows",
    "zh-Hant": " 排除完全空白的行"
  },
  "组内同一字段出现不同值时会报冲突；可在映射中选择取值规则。": {
    "en": "Different values for one field within a group are reported as a conflict; choose how the value is picked in the mapping.",
    "zh-Hant": "組內同一字段出現不同值時會報衝突；可在映射中選擇取值規則。"
  },
  "来源区域为 {v1} 行 × {v2} 列，目标区域「{v3}」为 {v4} 行 × {v5} 列。尺寸不一致，不能保存：请重新选择来源区域，或修改目标区域大小。": {
    "en": "The source region is {v1} rows × {v2} columns; the target region “{v3}” is {v4} rows × {v5} columns. The sizes differ, so this cannot be saved: select another source region or resize the target region.",
    "zh-Hant": "來源區域為 {v1} 行 × {v2} 列，目標區域「{v3}」為 {v4} 行 × {v5} 列。尺寸不一致，不能保存：請重新選擇來源區域，或修改目標區域大小。"
  },
  "列对应（按列名自动匹配；未匹配的列请手动选择）：": {
    "en": "Column matching (matched by name automatically; choose unmatched columns manually):",
    "zh-Hant": "列對應（按列名自動匹配；未匹配的列請手動選擇）："
  },
  "已有同名字段「{v1}」：与它填入相同的值": {
    "en": "A field named “{v1}” already exists: fill in the same value as that field",
    "zh-Hant": "已有同名字段「{v1}」：與它填入相同的值"
  },
  "创建后可在块内继续划选文字，选「作为内容块的一列」添加列；块内的其他字段每一份填入相同的值。": {
    "en": "After creating it, select more text inside the block and choose “Use as a column of block” to add columns; other fields inside the block get the same value in every copy.",
    "zh-Hant": "創建後可在塊內繼續劃選文字，選「作為內容塊的一列」添加列；塊內的其他字段每一份填入相同的值。"
  },
  "区域内原有文字会被删除，表格线保留；放不下时逐步缩小字号，仍放不下则该样本报错，不会截断。": {
    "en": "Existing text in the area is removed and table lines are kept; text that does not fit is shrunk step by step, and the sample fails rather than being truncated.",
    "zh-Hant": "區域內原有文字會被刪除，表格線保留；放不下時逐步縮小字號，仍放不下則該樣本報錯，不會截斷。"
  },
  "框内原有文字会被删除，不是遮盖。": {
    "en": "Existing text in the box is removed, not covered up.",
    "zh-Hant": "框內原有文字會被刪除，不是遮蓋。"
  },
  "框内原有内容按周围背景重建；文字颜色与字号沿用原内容。": {
    "en": "The box is repainted from the surrounding background; text color and size follow the original.",
    "zh-Hant": "框內原有內容按周圍背景重建；文字顏色與字號沿用原內容。"
  },
  "按真实排版显示页面与分页": {
    "en": "Show pages with the real layout and page breaks",
    "zh-Hant": "按真實排版顯示頁面與分頁"
  },
  "预览结果与批量生成一致": {
    "en": "The preview matches what batch generation produces",
    "zh-Hant": "預覽結果與批量生成一致"
  },
  "正在排版…": {
    "en": "Laying out…",
    "zh-Hant": "正在排版…"
  },
  "恢复某个版本会把它的内容存为新的当前版本；之后的版本都保留，随时可以撤销。": {
    "en": "Restoring a version saves its content as a new current version; later versions are kept and the restore can be undone at any time.",
    "zh-Hant": "恢復某個版本會把它的內容存為新的當前版本；之後的版本都保留，隨時可以撤銷。"
  },
  "有未保存为版本的修改；保存模板会把当前内容记为新版本。": {
    "en": "There are changes not yet saved as a version; saving the template records the current content as a new version.",
    "zh-Hant": "有未保存為版本的修改；保存模板會把當前內容記為新版本。"
  },
  "重复明细行：选一行中的连续单元格，上一行为表头；生成时按明细条数插入行。": {
    "en": "Repeating detail row: select consecutive cells in one row, with the row above as the header; rows are inserted per detail item at generation.",
    "zh-Hant": "重複明細行：選一行中的連續單元格，上一行為表頭；生成時按明細條數插入行。"
  },
  "还没有字段。选中单元格后用上方的「设为字段单元格 / 固定区域 / 重复明细行」。": {
    "en": "No fields yet. Select cells, then use “Set as cell field / fixed region / repeating detail row” above.",
    "zh-Hant": "還沒有字段。選中單元格後用上方的「設為字段單元格 / 固定區域 / 重複明細行」。"
  },
  "转换规则按从上到下的顺序执行。": {
    "en": "Transforms run in order from top to bottom.",
    "zh-Hant": "轉換規則按從上到下的順序執行。"
  },
  "回归用例保存项目的来源文件（不脱敏）、模板、样本规则、映射和每个样本的期望结果，只在本工作空间内可见。运行时重新解析和生成，逐个字段、逐段文字比较。": {
    "en": "A regression case stores the project's source files (as they are, not anonymized), template, sample rule, mappings and each sample's expected result, and is visible only in this workspace. Running it parses and generates again and compares every field and every passage of text.",
    "zh-Hant": "回歸用例保存項目的來源文件（不脫敏）、模板、樣本規則、映射和每個樣本的期望結果，只在本工作空間內可見。運行時重新解析和生成，逐個字段、逐段文字比較。"
  },
  "在项目的「更多」菜单中选择「保存为回归用例」。": {
    "en": "In a project, choose “Save as regression case” from the More menu.",
    "zh-Hant": "在項目的「更多」選單中選擇「保存為回歸用例」。"
  },
  "用例包含本项目的文件，可在「回归用例」页面运行和下载。请只保存测试数据或已脱敏的文件。": {
    "en": "The case contains this project's files and can be run and downloaded on the Regression cases page. Keep only test data or anonymized files in it.",
    "zh-Hant": "用例包含本項目的文件，可在「回歸用例」頁面運行和下載。請只保存測試數據或已脫敏的文件。"
  },
  "保存当前项目的来源文件、模板、样本规则和映射，并记录前 {v1} 个样本（共 {v2} 个）的期望结果，以后可以重新运行并逐项比较差异。": {
    "en": "Saves this project's source files, template, sample rule and mappings, and records the expected result of the first {v1} samples (of {v2}) so the case can be run again later and compared item by item. ",
    "zh-Hant": "保存當前項目的來源文件、模板、樣本規則和映射，並記錄前 {v1} 個樣本（共 {v2} 個）的期望結果，以後可以重新運行並逐項比較差異。"
  },
  "所有处理都在本服务器完成，文件不发送到外部服务。数值缩放与日期平移对整列使用同一比例或天数，保留合计与排序关系。": {
    "en": "Everything runs on this server; files are never sent to external services. Scaling and date shifting use one factor or offset per column, so totals and ordering are preserved.",
    "zh-Hant": "所有處理都在本伺服器完成，文件不發送到外部服務。數值縮放與日期平移對整列使用同一比例或天數，保留合計與排序關係。"
  },
  "均为开源组件；不使用付费组件或外部云服务": {
    "en": "All open-source components; no paid components or external cloud services",
    "zh-Hant": "均為開源組件；不使用付費組件或外部雲服務"
  },
  "当前部署：排版引擎 ": {
    "en": "This deployment: layout engine ",
    "zh-Hant": "當前部署：排版引擎 "
  },
  "；本地文字识别 ": {
    "en": "; local text recognition ",
    "zh-Hant": "；本機文字辨識 "
  },
  "）。未经验证的格式不标为支持。": {
    "en": "). Formats that have not been verified are not marked as supported.",
    "zh-Hant": "）。未經驗證的格式不標為支持。"
  },
  "副本在目标工作空间的管理员批准后创建，之后独立于原项目；原项目保持不变。": {
    "en": "The copy is created once an administrator of the target workspace approves it and is then independent of the original; the original project stays unchanged.",
    "zh-Hant": "副本在目標工作空間的管理員批准後創建，之後獨立於原項目；原項目保持不變。"
  },
  "在消息中讨论": {
    "en": "Discuss in Messages",
    "zh-Hant": "在訊息中討論"
  },
  "源项目在批准后有改动，批准范围不再有效；请重新申请。": {
    "en": "The source project changed after approval, so the approved scope no longer applies. Request the copy again.",
    "zh-Hant": "來源項目在批准後有改動，批准範圍不再有效；請重新申請。"
  },
  "批准已过期；请重新申请。": {
    "en": "The approval has expired. Request the copy again.",
    "zh-Hant": "批准已過期；請重新申請。"
  },
  "当前数据流政策或导出权限不允许复制到目标空间，请核对后重新申请。": {
    "en": "The current data transfer policy or export rights do not allow this copy. Check them before requesting it again.",
    "zh-Hant": "目前資料流政策或匯出權限不允許複製到目標空間，請核對後重新申請。"
  },
  "申请人不再拥有源项目。": {
    "en": "The requester no longer owns the source project.",
    "zh-Hant": "申請人不再擁有來源項目。"
  },
  "审批者不再具有审批权限；请重新申请。": {
    "en": "The approver no longer has approval rights. Request the copy again.",
    "zh-Hant": "審批者不再具有審批權限；請重新申請。"
  },
  "目标组织当前不可用。": {
    "en": "The target organization is currently unavailable.",
    "zh-Hant": "目標組織目前不可用。"
  },
  "你已不能把项目复制到目标工作空间；请确认你仍是它的编辑成员。": {
    "en": "You can no longer copy the project into the target workspace. Check that you are still an editor there.",
    "zh-Hant": "你已不能把項目複製到目標工作空間；請確認你仍是它的編輯成員。"
  },
  "源项目已不可用。": {
    "en": "The source project is no longer available.",
    "zh-Hant": "來源項目已不可用。"
  },
  "申请的状态已改变。": {
    "en": "The request's status has changed.",
    "zh-Hant": "申請的狀態已改變。"
  },
  "复制过程出错，没有创建任何内容；请稍后重新申请。": {
    "en": "Copying failed and nothing was created. Request the copy again later.",
    "zh-Hant": "複製過程出錯，沒有建立任何內容；請稍後重新申請。"
  },
  "第 {v1} 行（整行）": {
    "en": "Row {v1} (whole row)",
    "zh-Hant": "第 {v1} 行（整行）"
  },
  "第 {v1}–{v2} 行（整行）": {
    "en": "Rows {v1}–{v2} (whole rows)",
    "zh-Hant": "第 {v1}–{v2} 行（整行）"
  },
  "重复内容块（整行）": {
    "en": "Repeating block (whole rows)",
    "zh-Hant": "重復內容塊（整行）"
  },
  "第 {v1}–{v2} 行作为一个整体：每条明细（或每个分段）复制一份，公式、合并单元格和条件格式随之复制": {
    "en": "Rows {v1}–{v2} as one unit: copied once per detail row (or per section), with their formulas, merged cells and conditional formats",
    "zh-Hant": "第 {v1}–{v2} 行作為一個整體：每條明細（或每個分段）複製一份，公式、合併單元格和條件格式隨之複製"
  },
  "第 {v1} 行在内容块「{v2}」里：每一份内容块只写入它那一段的明细行（有几条就插入几行）": {
    "en": "Row {v1} is inside the block “{v2}”: each copy of the block receives only its own section's detail rows (one row per detail)",
    "zh-Hant": "第 {v1} 行在內容塊「{v2}」裡：每一份內容塊只寫入它那一段的明細行（有幾條就插入幾行）"
  },
  "创建后在块内选中单元格，选「作为内容块的一列」添加列；块内可再设一行重复明细，每一份只写入该段的行；块内的其他字段每一份填入相同的值。块内公式引用块外的单元格时，每一份都指向同一个单元格（不论有没有 $）。": {
    "en": "After creating it, select a cell inside the block and choose “Use as block column” to add a column; a row inside the block can be made a repeating detail row, and each copy then receives only its section's rows; other fields inside the block get the same value in every copy. A formula inside the block that refers to a cell outside it points at that same cell in every copy, with or without $.",
    "zh-Hant": "建立後在塊內選中單元格，選「作為內容塊的一列」添加列；塊內可再設一行重複明細，每一份只寫入該段的行；塊內的其他欄位每一份填入相同的值。塊內公式引用塊外的單元格時，每一份都指向同一個單元格（不論有沒有 $）。"
  },
  "重复内容块：第 {v1}–{v2} 行，每条明细（或每段）复制一份": {
    "en": "Repeating block: rows {v1}–{v2}, copied once per detail row (or per section)",
    "zh-Hant": "重復內容塊：第 {v1}–{v2} 行，每條明細（或每段）複製一份"
  },
  "块内公式引用块外的单元格时，每一份都指向同一个单元格（不论有没有 $）；单独的重复明细行则像 Excel 向下填充一样移动相对引用。": {
    "en": "A formula inside the block that refers to a cell outside it points at that same cell in every copy, with or without $. A repeating detail row on its own moves relative references the way Excel's fill down does.",
    "zh-Hant": "塊內公式引用塊外的單元格時，每一份都指向同一個單元格（不論有沒有 $）；單獨的重複明細行則像 Excel 向下填滿一樣移動相對引用。"
  },
  "块的列（在块内选中单元格可添加列）：": {
    "en": "Block columns (select a cell inside the block to add one):",
    "zh-Hant": "塊的列（在塊內選中單元格可添加列）："
  },
  "还没有列。请在右侧内容块中选中要随明细变化的单元格，选择「作为内容块的一列」。": {
    "en": "No columns yet. In the block on the right, select a cell that should change with the details and choose “Use as block column”.",
    "zh-Hant": "還沒有列。請在右側內容塊中選中要隨明細變化的單元格，選擇「作為內容塊的一列」。"
  },
  "内容块「{v1}」的列「{v2}」": {
    "en": "Column “{v2}” of the block “{v1}”",
    "zh-Hant": "內容塊「{v1}」的列「{v2}」"
  },
  // ---- live tables (docs-sheet-live.md §5, lib/sheettext.ts): status, closes, refusals, undo
  "同步中（{count}）": { "en": "Syncing ({count})", "zh-Hant": "同步中（{count}）" },
  "离线：{count} 处修改待同步": { "en": { "one": "Offline: {count} change waiting to sync", "other": "Offline: {count} changes waiting to sync" }, "zh-Hant": "離線：{count} 處修改待同步" },
  "离线：正在重新连接": { "en": "Offline: reconnecting", "zh-Hant": "離線：正在重新連線" },
  "这个标签页的实时连接已被另一个连接接管；请刷新页面继续。": { "en": "Another connection took over this tab's live connection. Reload the page to continue.", "zh-Hant": "這個分頁的即時連線已被另一個連線接管；請重新整理頁面繼續。" },
  "表格已被恢复或替换；请刷新页面继续。": { "en": "The table was restored or replaced. Reload the page to continue.", "zh-Hant": "表格已被還原或替換；請重新整理頁面繼續。" },
  "这张表格同时编辑的标签页已满；请稍后刷新再试。": { "en": "This table has as many editing tabs open as it can take. Reload later to try again.", "zh-Hant": "這張表格同時編輯的分頁已滿；請稍後重新整理再試。" },
  "你在这张表格上打开的可编辑标签页太多（最多 8 个）；请关闭其他标签页后刷新。": { "en": "You have too many editing tabs open on this table (at most 8). Close the others, then reload.", "zh-Hant": "你在這張表格上開啟的可編輯分頁太多（最多 8 個）；請關閉其他分頁後重新整理。" },
  "表格正在写入上一位成员的修改，稍后自动重新连接。": { "en": "The table is still writing the previous member's changes. Reconnecting shortly.", "zh-Hant": "表格正在寫入上一位成員的修改，稍後自動重新連線。" },
  "服务器暂时出错，正在自动重新连接。": { "en": "The server had a temporary error. Reconnecting.", "zh-Hant": "伺服器暫時出錯，正在自動重新連線。" },
  "连接中断，正在自动重新连接。": { "en": "The connection dropped. Reconnecting.", "zh-Hant": "連線中斷，正在自動重新連線。" },
  "这张表格有 {size} 个单元格，超过实时协同的上限（{max}）：改为保存时合并。": { "en": "This table has {size} cells, more than live collaboration allows ({max}): changes are merged when saved instead.", "zh-Hant": "這張表格有 {size} 個單元格，超過即時協作的上限（{max}）：改為儲存時合併。" },
  "这张表格的内容超过实时协同的大小上限：改为保存时合并。": { "en": "This table is larger than live collaboration allows: changes are merged when saved instead.", "zh-Hant": "這張表格的內容超過即時協作的大小上限：改為儲存時合併。" },
  "这张表格暂时不能实时协同：改为保存时合并。": { "en": "This table cannot be edited live for now: changes are merged when saved instead.", "zh-Hant": "這張表格暫時不能即時協作：改為儲存時合併。" },
  "其他成员": { "en": "Another member", "zh-Hant": "其他成員" },
  "值": { "en": "value", "zh-Hant": "值" },
  "类型": { "en": "type", "zh-Hant": "類型" },
  "底色": { "en": "fill colour", "zh-Hant": "底色" },
  "水平对齐": { "en": "horizontal alignment", "zh-Hant": "水平對齊" },
  "垂直对齐": { "en": "vertical alignment", "zh-Hant": "垂直對齊" },
  "行": { "en": "row", "zh-Hant": "行" },
  "列": { "en": "column", "zh-Hant": "列" },
  "合并区域": { "en": "merged range", "zh-Hant": "合併區域" },
  "{v1} 已先修改了 {v2}（现在是「{v3}」）。": { "en": "{v1} changed {v2} first (it is now “{v3}”).", "zh-Hant": "{v1} 已先修改了 {v2}（現在是「{v3}」）。" },
  "{v1} 在 {v2} 合并的 {v3} 内，只能在合并区域的左上角写入值。": { "en": "{v1} is inside {v3}, merged by {v2}: a merged range holds a value only in its top-left cell.", "zh-Hant": "{v1} 在 {v2} 合併的 {v3} 內，只能在合併區域的左上角寫入值。" },
  "无法合并 {v1}：它与 {v2} 合并的 {v3} 重叠。": { "en": "Cannot merge {v1}: it overlaps {v3}, merged by {v2}.", "zh-Hant": "無法合併 {v1}：它與 {v2} 合併的 {v3} 重疊。" },
  "无法合并 {v1}：会隐藏 {count} 个有值的单元格（{v2}），请先清空它们。": { "en": { "one": "Cannot merge {v1}: it would hide {count} cell with a value ({v2}). Clear them first.", "other": "Cannot merge {v1}: it would hide {count} cells with values ({v2}). Clear them first." }, "zh-Hant": "無法合併 {v1}：會隱藏 {count} 個有值的單元格（{v2}），請先清空它們。" },
  "字段 ID「{v1}」已被字段「{v2}」使用。": { "en": "Field ID “{v1}” is already used by field “{v2}”.", "zh-Hant": "字段 ID「{v1}」已被字段「{v2}」使用。" },
  "内部编号恰好冲突，请再做一次。": { "en": "An internal id happened to collide. Please do it again.", "zh-Hant": "內部編號恰好衝突，請再做一次。" },
  "已有名为「{v1}」的工作表（名称不区分大小写）。": { "en": "There is already a sheet named “{v1}” (names ignore case).", "zh-Hant": "已有名為「{v1}」的工作表（名稱不區分大小寫）。" },
  "工作表「{v1}」已被 {v2} 删除，修改未应用。": { "en": "Sheet “{v1}” was deleted by {v2}; the change was not applied.", "zh-Hant": "工作表「{v1}」已被 {v2} 刪除，修改未套用。" },
  "至少要保留一个工作表。": { "en": "At least one sheet must remain.", "zh-Hant": "至少要保留一個工作表。" },
  "要修改的{v1}已不存在（可能已被其他成员删除或移动）。": { "en": "The {v1} to change no longer exists (another member may have removed or moved it).", "zh-Hant": "要修改的{v1}已不存在（可能已被其他成員刪除或移動）。" },
  "表格在你看到之后已保存并整理过，{v1} 的修改未应用；请确认后再做一次。": { "en": "The table was saved and tidied since you last saw it; the change to {v1} was not applied. Check it, then do it again.", "zh-Hant": "表格在你看到之後已儲存並整理過，{v1} 的修改未套用；請確認後再做一次。" },
  "你现在只能查看这个模板，{v1} 的修改未应用。": { "en": "You can only view this template now; the change to {v1} was not applied.", "zh-Hant": "你現在只能檢視這個範本，{v1} 的修改未套用。" },
  "表格已退出实时协同，{v1} 的修改未应用。": { "en": "The table left live collaboration; the change to {v1} was not applied.", "zh-Hant": "表格已退出即時協作，{v1} 的修改未套用。" },
  "表格已从已保存的版本重新开始，{v1} 的修改未应用。": { "en": "The table started again from a saved version; the change to {v1} was not applied.", "zh-Hant": "表格已從已儲存的版本重新開始，{v1} 的修改未套用。" },
  "实时连接已断开，{v1} 的修改没有同步。": { "en": "The live connection closed; the change to {v1} was not synced.", "zh-Hant": "即時連線已中斷，{v1} 的修改沒有同步。" },
  "无法确认 {v1} 的修改是否已生效（连接中断期间服务器已不记得这个标签页）；请核对后决定是否再做一次。": { "en": "Whether the change to {v1} took effect cannot be confirmed (the server no longer remembered this tab while it was disconnected). Check it, then decide whether to do it again.", "zh-Hant": "無法確認 {v1} 的修改是否已生效（連線中斷期間伺服器已不記得這個分頁）；請核對後決定是否再做一次。" },
  "服务器处理 {v1} 的修改时出错，修改未应用。": { "en": "The server failed while applying the change to {v1}; it was not applied.", "zh-Hant": "伺服器處理 {v1} 的修改時出錯，修改未套用。" },
  "{v1} 的修改未应用（{v2}）。": { "en": "The change to {v1} was not applied ({v2}).", "zh-Hant": "{v1} 的修改未套用（{v2}）。" },
  "无法合并 {v1}：字段「{v2}」（{v3}）会落在合并区域内部。": { "en": "Cannot merge {v1}: field “{v2}” ({v3}) would be inside the merged range.", "zh-Hant": "無法合併 {v1}：字段「{v2}」（{v3}）會落在合併區域內部。" },
  "无法合并 {v1}：它与区域字段「{v2}」（{v3}）相交。": { "en": "Cannot merge {v1}: it crosses the range field “{v2}” ({v3}).", "zh-Hant": "無法合併 {v1}：它與區域字段「{v2}」（{v3}）相交。" },
  "无法合并 {v1}：它会纵向跨过重复明细行「{v2}」（{v3}）。": { "en": "Cannot merge {v1}: it would span the repeating row “{v2}” ({v3}) vertically.", "zh-Hant": "無法合併 {v1}：它會縱向跨過重復明細行「{v2}」（{v3}）。" },
  "无法把 {v1} 设为字段：它在 {v2} 合并的 {v3} 内（请用左上角）。": { "en": "{v1} cannot be a field: it is inside {v3}, merged by {v2} (use its top-left cell).", "zh-Hant": "無法把 {v1} 設為字段：它在 {v2} 合併的 {v3} 內（請用左上角）。" },
  "区域字段 {v1} 不能包含合并区域 {v2}。": { "en": "The range field {v1} cannot contain the merged range {v2}.", "zh-Hant": "區域字段 {v1} 不能包含合併區域 {v2}。" },
  "重复明细行 {v1} 与纵向合并的 {v2} 相交。": { "en": "The repeating row {v1} crosses the vertically merged {v2}.", "zh-Hant": "重復明細行 {v1} 與縱向合併的 {v2} 相交。" },
  "这一行已有重复明细行「{v1}」。": { "en": "This row already has the repeating row “{v1}”.", "zh-Hant": "這一行已有重復明細行「{v1}」。" },
  "字段位置 {v1} 与字段「{v2}」重叠。": { "en": "The field position {v1} overlaps field “{v2}”.", "zh-Hant": "字段位置 {v1} 與字段「{v2}」重疊。" },
  "单元格内容超过 {max} 个字符。": { "en": "The cell holds more than {max} characters.", "zh-Hant": "單元格內容超過 {max} 個字元。" },
  "公式超过 {max} 个字符。": { "en": "The formula is longer than {max} characters.", "zh-Hant": "公式超過 {max} 個字元。" },
  "表格已达到 {max} 个单元格的上限。": { "en": "The table has reached its limit of {max} cells.", "zh-Hant": "表格已達到 {max} 個單元格的上限。" },
  "行列总数已达上限（{max}）。": { "en": "The total of rows and columns has reached its limit ({max}).", "zh-Hant": "行列總數已達上限（{max}）。" },
  "行数已达上限（{max}）。": { "en": "The number of rows has reached its limit ({max}).", "zh-Hant": "行數已達上限（{max}）。" },
  "列数已达上限（{max}）。": { "en": "The number of columns has reached its limit ({max}).", "zh-Hant": "列數已達上限（{max}）。" },
  "合并区域过大（最多 {max} 个单元格）。": { "en": "The merged range is too large (at most {max} cells).", "zh-Hant": "合併區域過大（最多 {max} 個單元格）。" },
  "一次操作的步骤过多（最多 {max} 步）。": { "en": "Too many steps in one operation (at most {max}).", "zh-Hant": "一次操作的步驟過多（最多 {max} 步）。" },
  "工作表数量已达上限（{max}）。": { "en": "The number of sheets has reached its limit ({max}).", "zh-Hant": "工作表數量已達上限（{max}）。" },
  "表格已达到实时协同的大小上限：现在只能删减内容。": { "en": "The table has reached the size limit of live collaboration: only removing content is possible now.", "zh-Hant": "表格已達到即時協作的大小上限：現在只能刪減內容。" },
  "内容超过上限。": { "en": "The content is over the limit.", "zh-Hant": "內容超過上限。" },
  "{v1} 的{v2}无效。": { "en": "The {v2} of {v1} is not valid.", "zh-Hant": "{v1} 的{v2}無效。" },
  "{v1} 的内容含有无法写入 Excel 文件的字符。": { "en": "{v1} contains characters that cannot be written to an Excel file.", "zh-Hant": "{v1} 的內容含有無法寫入 Excel 檔案的字元。" },
  "数字格式无效（最多 255 个字符）。": { "en": "The number format is not valid (at most 255 characters).", "zh-Hant": "數字格式無效（最多 255 個字元）。" },
  "{v1} 的格式无效。": { "en": "The format of {v1} is not valid.", "zh-Hant": "{v1} 的格式無效。" },
  "行高或列宽须在 {min}–{max} 之间。": { "en": "Row heights and column widths must be between {min} and {max}.", "zh-Hant": "行高或列寬須在 {min}–{max} 之間。" },
  "这一行或列不在表格范围内。": { "en": "That row or column is outside the table.", "zh-Hant": "這一行或列不在表格範圍內。" },
  "至少选择两个单元格才能合并。": { "en": "Select at least two cells to merge.", "zh-Hant": "至少選擇兩個單元格才能合併。" },
  "工作表名称无效：不能为空，首尾不能有空格或单引号，不能含 \\ / * ? : [ ]，最长 {max} 个字符，也不能是 History。": { "en": "Not a valid sheet name: it cannot be empty, start or end with a space or apostrophe, contain \\ / * ? : [ ], be longer than {max} characters, or be History.", "zh-Hant": "工作表名稱無效：不能為空，首尾不能有空格或單引號，不能含 \\ / * ? : [ ]，最長 {max} 個字元，也不能是 History。" },
  "字段名称不能为空，最长 {max} 个字符。": { "en": "A field name cannot be empty and has at most {max} characters.", "zh-Hant": "字段名稱不能為空，最長 {max} 個字元。" },
  "字段设置无效。": { "en": "The field settings are not valid.", "zh-Hant": "字段設定無效。" },
  "一次最多添加 {max} 行或列。": { "en": "At most {max} rows or columns can be added at once.", "zh-Hant": "一次最多新增 {max} 行或列。" },
  "这次修改无效（{v1}）。": { "en": "This change is not valid ({v1}).", "zh-Hant": "這次修改無效（{v1}）。" },
  "{v1} 保存的新版本替换了你在 {v2} 的值。": { "en": "The new version {v1} saved replaced your value in {v2}.", "zh-Hant": "{v1} 儲存的新版本取代了你在 {v2} 的值。" },
  "新增的行和列不能撤销。": { "en": "Rows and columns that were added cannot be undone.", "zh-Hant": "新增的行和列不能撤銷。" },
  "没有 ID 的字段「{v1}」不能恢复。": { "en": "Field “{v1}” has no ID and cannot be brought back.", "zh-Hant": "沒有 ID 的字段「{v1}」不能恢復。" },
  "未撤销 {v1}：其他成员之后改过它。": { "en": "{v1} was not undone: another member changed it since.", "zh-Hant": "未撤銷 {v1}：其他成員之後改過它。" },
  "{v1} 修改了 {count} 个单元格（{v2} 等）": { "en": { "one": "{v1} changed {count} cell ({v2})", "other": "{v1} changed {count} cells ({v2} and others)" }, "zh-Hant": "{v1} 修改了 {count} 個單元格（{v2} 等）" },
  "{v1} 修改了 {v2}": { "en": "{v1} changed {v2}", "zh-Hant": "{v1} 修改了 {v2}" },
  "内容与已保存的第 {v1} 版相同，无需新版本。": { "en": "The content is the same as saved version {v1}; no new version is needed.", "zh-Hant": "內容與已儲存的第 {v1} 版相同，無需新版本。" },
  "已保存为第 {v1} 版；表格稍后标记为已保存。": { "en": "Saved as version {v1}; the table will be marked saved shortly.", "zh-Hant": "已儲存為第 {v1} 版；表格稍後標記為已儲存。" },
  "已保存为第 {v1} 版。": { "en": "Saved as version {v1}.", "zh-Hant": "已儲存為第 {v1} 版。" },
  "表格已不在实时协同中，不能这样保存；请刷新页面。": { "en": "The table is no longer in live collaboration and cannot be saved this way. Reload the page.", "zh-Hant": "表格已不在即時協作中，不能這樣儲存；請重新整理頁面。" },
  "刚有人保存了新版本，表格正在合并它；请稍后再保存。": { "en": "Someone just saved a new version and the table is merging it. Save again in a moment.", "zh-Hant": "剛有人儲存了新版本，表格正在合併它；請稍後再儲存。" },
  "、": { "en": ", ", "zh-Hant": "、" },
  "这一步撤销没有完成；再按撤销会撤销更早的修改。": { "en": "This undo did not happen; undo again to undo earlier changes.", "zh-Hant": "這一步撤銷沒有完成；再按撤銷會撤銷更早的修改。" },
  "你在另一个标签页里已先修改了 {v1}（现在是「{v2}」）。": { "en": "You changed {v1} first in another tab (now “{v2}”).", "zh-Hant": "你在另一個分頁裡已先修改了 {v1}（現在是「{v2}」）。" },
  "{v1}（{v3} 改为「{v2}」）": { "en": "{v1} (changed to “{v2}” by {v3})", "zh-Hant": "{v1}（{v3} 改為「{v2}」）" },
  "其中 {v1}{v2} 在你之后被改过；再做一次会覆盖它们。": { "en": "Of its cells, {v1}{v2} changed after you did; doing it again would overwrite them.", "zh-Hant": "其中 {v1}{v2} 在你之後被改過；再做一次會覆蓋它們。" },
  "{v1}（现在是「{v2}」）": { "en": "{v1} (now “{v2}”)", "zh-Hant": "{v1}（現在是「{v2}」）" },
  "等 {count} 处": { "en": { "one": " ({count} in all)", "other": " ({count} in all)" }, "zh-Hant": "等 {count} 處" },
  "同一操作里 {v1}{v2} 在你之后也被改过；再发送你的修改会覆盖它们。": { "en": "In the same operation, {v1}{v2} also changed after you did; sending your change again would overwrite them.", "zh-Hant": "同一操作裡 {v1}{v2} 在你之後也被改過；再傳送你的修改會覆蓋它們。" },
  "表格已从已保存的版本重新开始，你在 {v1} 的修改没有保留下来。": { "en": "The table started again from a saved version; your change to {v1} was not kept.", "zh-Hant": "表格已從已儲存的版本重新開始，你在 {v1} 的修改沒有保留下來。" },
  "工作表「{v1}」之后有其他人的修改（或不再是空表），撤销不会删除它；确实要删除，请用「删除当前表」。": { "en": "Sheet “{v1}” has changes by others since (or is no longer empty), so undo does not delete it; to delete it anyway, use “Delete current sheet”.", "zh-Hant": "工作表「{v1}」之後有其他人的修改（或不再是空表），撤銷不會刪除它；確實要刪除，請用「刪除當前表」。" },
  "有人另外保存的新版本无法并入这里的修改，这里的修改暂时不能保存为版本：先把需要的内容复制出来，再请有权限的成员确认放弃这些实时修改。": { "en": "A version someone saved separately cannot be merged into the changes here, so they cannot be saved as a version for now: copy out what you need, then ask a member with permission to confirm discarding these live changes.", "zh-Hant": "有人另外儲存的新版本無法併入這裡的修改，這裡的修改暫時不能儲存為版本：先把需要的內容複製出來，再請有權限的成員確認放棄這些即時修改。" },
  // ---- the table editor in live mode (pages/SheetEditor.tsx)
  "实时协同已连上，但你已经开始单人编辑：继续单人编辑，保存时按单元格合并。": { "en": "Live editing is now available, but you have already started editing alone: you keep editing alone, and your changes are merged cell by cell when you save.", "zh-Hant": "即時協作已連上，但你已經開始單人編輯：繼續單人編輯，儲存時按單元格合併。" },
  "{v1} 已{v2}第 {v3} 版": { "en": "{v1}: {v2} version {v3}", "zh-Hant": "{v1} 已{v2}第 {v3} 版" },
  "还有修改没有同步？": { "en": "Changes not synced yet?", "zh-Hant": "還有修改沒有同步？" },
  "有 {count} 处修改还没有送到服务器，离开后会丢失。": { "en": { "one": "{count} change has not reached the server yet and will be lost if you leave.", "other": "{count} changes have not reached the server yet and will be lost if you leave." }, "zh-Hant": "有 {count} 處修改還沒有送到伺服器，離開後會遺失。" },
  "仍然离开": { "en": "Leave anyway", "zh-Hant": "仍然離開" },
  "先添加一个工作表。": { "en": "Add a sheet first.", "zh-Hant": "先新增一個工作表。" },
  "你现在只能查看这个模板。": { "en": "You can only view this template now.", "zh-Hant": "你現在只能檢視這個範本。" },
  "现在不能修改：实时协同尚未就绪。": { "en": "Changes are not possible yet: live editing is not ready.", "zh-Hant": "現在不能修改：即時協作尚未就緒。" },
  "没有可撤销的修改。": { "en": "Nothing of yours to undo.", "zh-Hant": "沒有可撤銷的修改。" },
  "合并会清除其他单元格的值": { "en": "Merging clears the other cells' values", "zh-Hant": "合併會清除其他單元格的值" },
  "合并 {v1} 只保留左上角的值；{v2} 的内容会被清除。": { "en": "Merging {v1} keeps only the top-left value; the content of {v2} will be cleared.", "zh-Hant": "合併 {v1} 只保留左上角的值；{v2} 的內容會被清除。" },
  "清除并合并": { "en": "Clear and merge", "zh-Hant": "清除並合併" },
  "删除工作表「{v1}」？": { "en": "Delete sheet “{v1}”?", "zh-Hant": "刪除工作表「{v1}」？" },
  "实时协同已恢复，但你在单人编辑中有未保存的修改：继续单人编辑，保存后回到实时协同。": { "en": "Live editing is back, but you have unsaved changes made alone: you keep editing alone until you save, then return to live editing.", "zh-Hant": "即時協作已恢復，但你在單人編輯中有未儲存的修改：繼續單人編輯，儲存後回到即時協作。" },
  "已删除的工作表（{count}）": { "en": { "one": "Deleted sheet ({count})", "other": "Deleted sheets ({count})" }, "zh-Hant": "已刪除的工作表（{count}）" },
  "恢复已删除的工作表": { "en": "Restore a deleted sheet", "zh-Hant": "恢復已刪除的工作表" },
  "已删除的工作表（{count}）：保存为版本之前，可以在这里恢复。": { "en": { "one": "Deleted sheet ({count}): until the table is saved as a version, it can be restored here.", "other": "Deleted sheets ({count}): until the table is saved as a version, they can be restored here." }, "zh-Hant": "已刪除的工作表（{count}）：儲存為版本之前，可以在這裡恢復。" },
  "要恢复的工作表": { "en": "Sheet to restore", "zh-Hant": "要恢復的工作表" },
  "恢复工作表": { "en": "Restore sheet", "zh-Hant": "恢復工作表" },
  "正在这张表上编辑的成员会一起看到它被删除；保存为版本之前，可以从「已删除的工作表」恢复。": { "en": "Members editing this table will see it deleted too. Until the table is saved as a version, it can be restored from “Deleted sheets”.", "zh-Hant": "正在這張表上編輯的成員會一起看到它被刪除；儲存為版本之前，可以從「已刪除的工作表」恢復。" },
  "工作表「{v1}」已被删除；保存为版本之前，可以从「已删除的工作表」恢复。": { "en": "Sheet “{v1}” was deleted. Until the table is saved as a version, it can be restored from “Deleted sheets”.", "zh-Hant": "工作表「{v1}」已被刪除；儲存為版本之前，可以從「已刪除的工作表」恢復。" },
  "工作表「{v1}」已被删除，你在 {v2} 输入的「{v3}」没有写入；保存为版本之前，可以从「已删除的工作表」恢复这张表。": { "en": "Sheet “{v1}” was deleted; what you typed in {v2} (“{v3}”) was not written. Until the table is saved as a version, the sheet can be restored from “Deleted sheets”.", "zh-Hant": "工作表「{v1}」已被刪除，你在 {v2} 輸入的「{v3}」沒有寫入；儲存為版本之前，可以從「已刪除的工作表」恢復這張表。" },
  "工作表「{v1}」已被删除。": { "en": "Sheet “{v1}” was deleted.", "zh-Hant": "工作表「{v1}」已被刪除。" },
  "工作表「{v1}」已恢复。": { "en": "Sheet “{v1}” was restored.", "zh-Hant": "工作表「{v1}」已恢復。" },
  "；": { "en": "; ", "zh-Hant": "；" },
  "登录已失效：实时协同已停止。下面没有同步的修改只在这个页面里：先复制需要的值，再重新登录并刷新。": { "en": "Your sign-in ended: live collaboration stopped. The unsynced changes below exist only on this page: copy the values you need, then sign in again and reload.", "zh-Hant": "登入已失效：即時協同已停止。下面沒有同步的修改只在這個頁面裡：先複製需要的值，再重新登入並重新整理。" },
  "删除工作表": { "en": "Delete sheet", "zh-Hant": "刪除工作表" },
  "还有 {count} 处修改没有同步，稍后再保存。": { "en": { "one": "{count} change is not synced yet. Save again in a moment.", "other": "{count} changes are not synced yet. Save again in a moment." }, "zh-Hant": "還有 {count} 處修改沒有同步，稍後再儲存。" },
  "服务器正在保存草稿": { "en": "The server is saving the draft", "zh-Hant": "伺服器正在儲存草稿" },
  "只能查看": { "en": "View only", "zh-Hant": "只能檢視" },
  "外部保存的新版本无法并入这里的修改：你们的修改都还在；保存会被拒绝，直到有权限的成员确认放弃它们。": { "en": "A version saved outside could not be merged into the changes here. Your changes are all still here; saving is refused until a member with permission confirms discarding them.", "zh-Hant": "外部儲存的新版本無法併入這裡的修改：你們的修改都還在；儲存會被拒絕，直到有權限的成員確認放棄它們。" },
  "撤销我的上一步修改": { "en": "Undo my last change", "zh-Hant": "撤銷我的上一步修改" },
  "撤销我的上一步修改（⌘Z）；别人之后改过的单元格不会被撤销": { "en": "Undo my last change (⌘Z); cells others changed since are left as they are", "zh-Hant": "撤銷我的上一步修改（⌘Z）；別人之後改過的單元格不會被撤銷" },
  "需要你处理": { "en": "Needs you", "zh-Hant": "需要你處理" },
  "需要你处理（{count}）": { "en": "Needs you ({count})", "zh-Hant": "需要你處理（{count}）" },
  "用我的值": { "en": "Use my value", "zh-Hant": "用我的值" },
  "保留对方的": { "en": "Keep theirs", "zh-Hant": "保留對方的" },
  "再做一次": { "en": "Do it again", "zh-Hant": "再做一次" },
  "不用了": { "en": "Leave it", "zh-Hant": "不用了" },
  "复制我的值": { "en": "Copy my value", "zh-Hant": "複製我的值" },
  "{v1} 刚修改": { "en": "Just changed by {v1}", "zh-Hant": "{v1} 剛修改" },
  "{v1}（有其他成员的新修改）": { "en": "{v1} (new changes by others)", "zh-Hant": "{v1}（有其他成員的新修改）" },
  "添加工作表": { "en": "Add a sheet", "zh-Hant": "新增工作表" },
} satisfies Messages;
