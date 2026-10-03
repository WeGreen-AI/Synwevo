import { registerMessages } from "@synwevo/ui/i18n-core";

export const dataprep = {
  "撤销编辑": { en: "Undo edit", "zh-Hant": "復原編輯" },
  "内容含不支持的表格字符，请导出 JSON": { en: "Content contains unsupported spreadsheet characters; export JSON instead", "zh-Hant": "內容含不支援的表格字元，請匯出 JSON" },
  "来源权限已改变，无法显示历史结果": { en: "Source permissions changed; the historical result cannot be displayed", "zh-Hant": "來源權限已改變，無法顯示歷史結果" },
  "CSV 防止公式注入；XLSX 单元格按文本导出以保留精度。JSON 保留空值、缺失和完整来源。下载仍受工作空间导出权限限制。": {
    "en": "CSV neutralizes formulas; XLSX exports text to preserve precision. JSON preserves nulls, missing values and complete provenance. Downloads require workspace export permission.",
    "zh-Hant": "CSV 防止公式注入；XLSX 儲存格以文字匯出以保留精度。JSON 保留空值、缺失和完整來源。下載仍受工作空間匯出權限限制。"
  },
  "{n} 行 · {c} 列": {
    "en": "{n} rows · {c} columns",
    "zh-Hant": "{n} 列 · {c} 欄"
  },
  "上一页": {
    "en": "Previous page",
    "zh-Hant": "上一頁"
  },
  "上传并检查工作表": {
    "en": "Upload and inspect sheets",
    "zh-Hant": "上傳並檢查工作表"
  },
  "上移步骤": {
    "en": "Move step up",
    "zh-Hant": "上移步驟"
  },
  "下一页": {
    "en": "Next page",
    "zh-Hant": "下一頁"
  },
  "下移步骤": {
    "en": "Move step down",
    "zh-Hant": "下移步驟"
  },
  "下载完整异常清单": {
    "en": "Download all issues",
    "zh-Hant": "下載完整異常清單"
  },
  "不接受宏或外部链接": {
    "en": "Macros and external links are not accepted",
    "zh-Hant": "不接受巨集或外部連結"
  },
  "不是有效的十进制数值": {
    "en": "Not a valid decimal number",
    "zh-Hant": "不是有效的十進位數值"
  },
  "主来源名称": {
    "en": "Primary source name",
    "zh-Hant": "主要來源名稱"
  },
  "仅保留匹配行": {
    "en": "Keep matching rows only",
    "zh-Hant": "僅保留相符列"
  },
  "仅导入当前空间中你有权读取的提交；创建独立数据快照，保留字段编号。": {
    "en": "Import only submissions you can read in this workspace; create an independent snapshot preserving field IDs.",
    "zh-Hant": "僅匯入目前空間中你有權讀取的提交；建立獨立資料快照，保留欄位編號。"
  },
  "从原始数据到可追溯结果，每次处理固定来源与方案版本。": {
    "en": "Turn source data into traceable results, with inputs and recipe versions pinned for every run.",
    "zh-Hant": "從原始資料到可追溯結果，每次處理固定來源與方案版本。"
  },
  "以相同输入重新运行": {
    "en": "Run again with the same inputs",
    "zh-Hant": "以相同輸入重新執行"
  },
  "你没有导出权限": {
    "en": "You do not have export permission",
    "zh-Hant": "你沒有匯出權限"
  },
  "使用了文件缓存公式值，可能不是最新计算结果。": {
    "en": "Cached formula values were used; they may be out of date.",
    "zh-Hant": "使用了檔案快取公式值，可能不是最新計算結果。"
  },
  "使用缓存值（不重新计算）": {
    "en": "Use cached values (no recalculation)",
    "zh-Hant": "使用快取值（不重新計算）"
  },
  "保存草稿": {
    "en": "Save draft",
    "zh-Hant": "儲存草稿"
  },
  "保留并标记异常": {
    "en": "Keep rows and report issues",
    "zh-Hant": "保留並標記異常"
  },
  "保留最后一行": {
    "en": "Keep last row",
    "zh-Hant": "保留最後一列"
  },
  "保留本表全部行": {
    "en": "Keep all primary rows",
    "zh-Hant": "保留本表全部列"
  },
  "保留第一行": {
    "en": "Keep first row",
    "zh-Hant": "保留第一列"
  },
  "保留规则": {
    "en": "Retention rule",
    "zh-Hant": "保留規則"
  },
  "停用后不能新建此版本的运行，已有运行继续。确认？": {
    "en": "Retire this version? New runs will be blocked; existing runs continue.",
    "zh-Hant": "停用後不能新建此版本的執行，既有執行繼續。確認？"
  },
  "停用所选版本": {
    "en": "Retire selected version",
    "zh-Hant": "停用所選版本"
  },
  "允许同类空值与缺失键匹配": {
    "en": "Match null keys with nulls, and missing keys with missing keys",
    "zh-Hant": "允許同類空值與缺失鍵相符"
  },
  "全部匹配（多对多）": {
    "en": "All matches (many-to-many)",
    "zh-Hant": "全部相符（多對多）"
  },
  "全量运行所选版本": {
    "en": "Run selected version on all rows",
    "zh-Hant": "完整執行所選版本"
  },
  "公式处理": {
    "en": "Formula policy",
    "zh-Hant": "公式處理"
  },
  "公式没有缓存值": {
    "en": "Formula has no cached value",
    "zh-Hant": "公式沒有快取值"
  },
  "关联匹配": {
    "en": "Join sources",
    "zh-Hant": "關聯比對"
  },
  "关联展开超过处理规模限制": {
    "en": "Join expansion exceeds the processing limit",
    "zh-Hant": "關聯展開超過處理規模限制"
  },
  "关联方式": {
    "en": "Join type",
    "zh-Hant": "關聯方式"
  },
  "关联键重复，请选择首个匹配或全部匹配": {
    "en": "Duplicate join keys: choose first or all matches",
    "zh-Hant": "關聯鍵重複，請選擇首個相符或全部相符"
  },
  "分组列（可留空）": {
    "en": "Group columns (optional)",
    "zh-Hant": "分組欄（可留空）"
  },
  "分组汇总": {
    "en": "Group and aggregate",
    "zh-Hant": "分組彙總"
  },
  "分隔文本": {
    "en": "Separator text",
    "zh-Hant": "分隔文字"
  },
  "分隔符": {
    "en": "Delimiter",
    "zh-Hant": "分隔符號"
  },
  "列": {
    "en": "Column",
    "zh-Hant": "欄"
  },
  "列名必须唯一且不能为空": {
    "en": "Column names must be unique and nonempty",
    "zh-Hant": "欄名必須唯一且不能為空"
  },
  "列对齐": {
    "en": "Column alignment",
    "zh-Hant": "欄對齊"
  },
  "删除参数": {
    "en": "Remove parameter",
    "zh-Hant": "刪除參數"
  },
  "删除指标": {
    "en": "Remove measure",
    "zh-Hant": "刪除指標"
  },
  "删除步骤": {
    "en": "Remove step",
    "zh-Hant": "刪除步驟"
  },
  "制表符": {
    "en": "Tab",
    "zh-Hant": "定位字元"
  },
  "前后对比（仅所选样本）": {
    "en": "Before and after (selected samples only)",
    "zh-Hant": "前後比較（僅所選樣本）"
  },
  "单元格内容超过限制": {
    "en": "Cell content exceeds the limit",
    "zh-Hant": "儲存格內容超過限制"
  },
  "去重": {
    "en": "Remove duplicates",
    "zh-Hant": "移除重複"
  },
  "参数名称": {
    "en": "Parameter name",
    "zh-Hant": "參數名稱"
  },
  "参数未在方案中声明": {
    "en": "Parameter is not declared in the recipe",
    "zh-Hant": "參數未在方案中宣告"
  },
  "发布与版本": {
    "en": "Publishing and versions",
    "zh-Hant": "發佈與版本"
  },
  "发布保存的草稿": {
    "en": "Publish saved draft",
    "zh-Hant": "發佈儲存的草稿"
  },
  "取消运行": {
    "en": "Cancel run",
    "zh-Hant": "取消執行"
  },
  "取第一个匹配": {
    "en": "First match",
    "zh-Hant": "取第一個相符"
  },
  "可选择多列；顺序按列列表": {
    "en": "Select multiple columns; order follows the column list",
    "zh-Hant": "可選擇多欄；順序依欄列表"
  },
  "合并全部列，缺少的保留缺失": {
    "en": "Union columns; preserve missing values",
    "zh-Hant": "合併全部欄，缺少的保留缺失"
  },
  "合并字段": {
    "en": "Combine fields",
    "zh-Hant": "合併欄位"
  },
  "合计": {
    "en": "Sum",
    "zh-Hant": "合計"
  },
  "固定输入": {
    "en": "Pinned inputs",
    "zh-Hant": "固定輸入"
  },
  "处理中…": {
    "en": "Processing…",
    "zh-Hant": "處理中…"
  },
  "处理前": {
    "en": "Before",
    "zh-Hant": "處理前"
  },
  "处理后": {
    "en": "After",
    "zh-Hant": "處理後"
  },
  "处理失败，请重试": {
    "en": "Processing failed; try again",
    "zh-Hant": "處理失敗，請重試"
  },
  "处理方案": {
    "en": "Recipes",
    "zh-Hant": "處理方案"
  },
  "处理步骤": {
    "en": "Processing steps",
    "zh-Hant": "處理步驟"
  },
  "大于": {
    "en": "Greater than",
    "zh-Hant": "大於"
  },
  "大于或等于": {
    "en": "Greater than or equal",
    "zh-Hant": "大於或等於"
  },
  "实际进度：步骤 {s}，已处理 {n} / {total} 行": {
    "en": "Actual progress: step {s}, processed {n} / {total} rows",
    "zh-Hant": "實際進度：步驟 {s}，已處理 {n} / {total} 列"
  },
  "导入完整范围": {
    "en": "Import complete range",
    "zh-Hant": "匯入完整範圍"
  },
  "导入异常": {
    "en": "Import issues",
    "zh-Hant": "匯入異常"
  },
  "导入数据": {
    "en": "Import data",
    "zh-Hant": "匯入資料"
  },
  "导入来源": {
    "en": "Import source",
    "zh-Hant": "匯入來源"
  },
  "导入表单提交": {
    "en": "Import form submissions",
    "zh-Hant": "匯入表單提交"
  },
  "导出 {format}": {
    "en": "Export {format}",
    "zh-Hant": "匯出 {format}"
  },
  "小于": {
    "en": "Less than",
    "zh-Hant": "小於"
  },
  "小于或等于": {
    "en": "Less than or equal",
    "zh-Hant": "小於或等於"
  },
  "工作空间存储限额已达上限": {
    "en": "Workspace storage quota reached",
    "zh-Hant": "工作空間儲存限額已達上限"
  },
  "工作表": {
    "en": "Worksheet",
    "zh-Hant": "工作表"
  },
  "工作表不存在": {
    "en": "Worksheet not found",
    "zh-Hant": "工作表不存在"
  },
  "工作表包含错误单元格": {
    "en": "Worksheet contains an error cell",
    "zh-Hant": "工作表包含錯誤儲存格"
  },
  "布尔值": {
    "en": "Boolean",
    "zh-Hant": "布林值"
  },
  "布尔值无效": {
    "en": "Invalid boolean value",
    "zh-Hant": "布林值無效"
  },
  "异常": {
    "en": "Issues",
    "zh-Hant": "異常"
  },
  "异常原因": {
    "en": "Issue description",
    "zh-Hant": "異常原因"
  },
  "异常数量超过限制": {
    "en": "Issue count exceeds the limit",
    "zh-Hant": "異常數量超過限制"
  },
  "异常行": {
    "en": "Row issues",
    "zh-Hant": "異常列"
  },
  "异常行处理": {
    "en": "Invalid row policy",
    "zh-Hant": "異常列處理"
  },
  "异常说明": {
    "en": "Issue message",
    "zh-Hant": "異常說明"
  },
  "归档会阻止编辑和新建运行，已有结果保留。确认切换状态？": {
    "en": "Archiving blocks edits and new runs while retaining results. Change archive status?",
    "zh-Hant": "封存會阻止編輯和新建執行，既有結果保留。確認切換狀態？"
  },
  "归档方案": {
    "en": "Archive recipe",
    "zh-Hant": "封存方案"
  },
  "当前范围没有异常": {
    "en": "No issues in this range",
    "zh-Hant": "目前範圍沒有異常"
  },
  "恢复方案": {
    "en": "Restore recipe",
    "zh-Hant": "還原方案"
  },
  "打开处理方案": {
    "en": "Open recipe",
    "zh-Hant": "開啟處理方案"
  },
  "打开结果": {
    "en": "Open result",
    "zh-Hant": "開啟結果"
  },
  "拆分字段": {
    "en": "Split field",
    "zh-Hant": "拆分欄位"
  },
  "拒绝公式": {
    "en": "Reject formulas",
    "zh-Hant": "拒絕公式"
  },
  "拒绝精度损失": {
    "en": "Reject precision loss",
    "zh-Hant": "拒絕精度損失"
  },
  "按精确数值排序": {
    "en": "Sort by exact numeric value",
    "zh-Hant": "依精確數值排序"
  },
  "按精确数值比较": {
    "en": "Compare exact numeric values",
    "zh-Hant": "依精確數值比較"
  },
  "排序": {
    "en": "Sort",
    "zh-Hant": "排序"
  },
  "排队数量已达上限": {
    "en": "Queue limit reached",
    "zh-Hant": "排隊數量已達上限"
  },
  "排除异常行": {
    "en": "Exclude invalid rows",
    "zh-Hant": "排除異常列"
  },
  "提交编号（每行一个，最多 100 个）": {
    "en": "Submission IDs (one per line, up to 100)",
    "zh-Hant": "提交編號（每行一個，最多 100 個）"
  },
  "数值列": {
    "en": "Numeric column",
    "zh-Hant": "數值欄"
  },
  "数据或方案不存在": {
    "en": "Dataset or recipe not found",
    "zh-Hant": "資料或方案不存在"
  },
  "数据超过处理规模限制": {
    "en": "Data exceeds processing limits",
    "zh-Hant": "資料超過處理規模限制"
  },
  "数据集": {
    "en": "Datasets",
    "zh-Hant": "資料集"
  },
  "数据集名称": {
    "en": "Dataset name",
    "zh-Hant": "資料集名稱"
  },
  "数据预览": {
    "en": "Data preview",
    "zh-Hant": "資料預覽"
  },
  "数据默认按文本保留。首行作为列名；空字符串、空值和缺失分别记录。XLSX 公式默认拒绝，宏和外部链接不接受。": {
    "en": "Values stay as text by default. The first selected row names columns; empty strings, nulls and missing values stay distinct. XLSX formulas are rejected by default; macros and external links are refused.",
    "zh-Hant": "資料預設以文字保留。首列作為欄名；空字串、空值和缺失分別記錄。XLSX 公式預設拒絕，巨集和外部連結不接受。"
  },
  "整数": {
    "en": "Integer",
    "zh-Hant": "整數"
  },
  "文件为空或超过 8 MB": {
    "en": "File is empty or exceeds 8 MB",
    "zh-Hant": "檔案為空或超過 8 MB"
  },
  "文件展开后超过限制": {
    "en": "Expanded file exceeds the limit",
    "zh-Hant": "檔案展開後超過限制"
  },
  "文件最多 8 MB；每次处理最多 50,000 行、64 列、500,000 个单元格。": {
    "en": "Files: up to 8 MB. Each run: up to 50,000 rows, 64 columns and 500,000 cells.",
    "zh-Hant": "檔案最多 8 MB；每次處理最多 50,000 列、64 欄、500,000 個儲存格。"
  },
  "文件检查通过": {
    "en": "File check passed",
    "zh-Hant": "檔案檢查通過"
  },
  "文件结构无效": {
    "en": "Invalid file structure",
    "zh-Hant": "檔案結構無效"
  },
  "文件编码": {
    "en": "File encoding",
    "zh-Hant": "檔案編碼"
  },
  "文本": {
    "en": "Text",
    "zh-Hant": "文字"
  },
  "新建方案": {
    "en": "New recipe",
    "zh-Hant": "新增方案"
  },
  "方案参数": {
    "en": "Recipe parameters",
    "zh-Hant": "方案參數"
  },
  "方案名称": {
    "en": "Recipe name",
    "zh-Hant": "方案名稱"
  },
  "方案已归档": {
    "en": "Recipe is archived",
    "zh-Hant": "方案已封存"
  },
  "方案已被修改，请重新载入": {
    "en": "Recipe changed elsewhere; reload before saving",
    "zh-Hant": "方案已被修改，請重新載入"
  },
  "方案版本不存在": {
    "en": "Recipe version not found",
    "zh-Hant": "方案版本不存在"
  },
  "方案说明": {
    "en": "Recipe description",
    "zh-Hant": "方案說明"
  },
  "无效转换行被排除并记录；校验步骤可选择保留。日期时间统一转为 UTC；排序稳定；相同固定输入可重现。": {
    "en": "Invalid conversion rows are excluded and reported; validation can keep them. Timestamps convert to UTC; sorting is stable; pinned inputs produce reproducible results.",
    "zh-Hant": "無效轉換列被排除並記錄；驗證步驟可選擇保留。日期時間統一轉為 UTC；排序穩定；相同固定輸入可重現。"
  },
  "无时区时间的固定偏移": {
    "en": "Fixed offset for local timestamps",
    "zh-Hant": "無時區時間的固定偏移"
  },
  "无法按所选编码读取文件": {
    "en": "Cannot read the selected encoding",
    "zh-Hant": "無法依所選編碼讀取檔案"
  },
  "日期无效": {
    "en": "Invalid date",
    "zh-Hant": "日期無效"
  },
  "日期时间无效": {
    "en": "Invalid timestamp",
    "zh-Hant": "日期時間無效"
  },
  "日期时间需要明确时区": {
    "en": "Timestamp requires an explicit timezone",
    "zh-Hant": "日期時間需要明確時區"
  },
  "是空值": {
    "en": "Is null",
    "zh-Hant": "是空值"
  },
  "是空字符串": {
    "en": "Is empty string",
    "zh-Hant": "是空字串"
  },
  "是缺失": {
    "en": "Is missing",
    "zh-Hant": "是缺失"
  },
  "有未保存修改": {
    "en": "Unsaved changes",
    "zh-Hant": "有未儲存修改"
  },
  "有空值则结果为空": {
    "en": "Propagate nulls",
    "zh-Hant": "有空值則結果為空"
  },
  "未保存草稿不会进入全量运行。全量运行只使用所选发布版本。": {
    "en": "Unsaved changes are not part of a full run. Full runs use only the selected published version.",
    "zh-Hant": "未儲存草稿不會進入完整執行。完整執行只使用所選發佈版本。"
  },
  "未命名来源": {
    "en": "Unnamed source",
    "zh-Hant": "未命名來源"
  },
  "未通过校验规则": {
    "en": "Validation rule failed",
    "zh-Hant": "未通過驗證規則"
  },
  "未配置文件扫描服务；未声明扫描通过": {
    "en": "File scanning is not configured; no scan clearance is claimed",
    "zh-Hant": "未設定檔案掃描服務；未宣告掃描通過"
  },
  "本表关联键": {
    "en": "Primary join keys",
    "zh-Hant": "本表關聯鍵"
  },
  "来源": {
    "en": "Source",
    "zh-Hant": "來源"
  },
  "来源关联键": {
    "en": "Other source join keys",
    "zh-Hant": "來源關聯鍵"
  },
  "来源列前缀": {
    "en": "Joined column prefix",
    "zh-Hant": "來源欄前綴"
  },
  "来源行": {
    "en": "Source row",
    "zh-Hant": "來源列"
  },
  "标题行": {
    "en": "Header row",
    "zh-Hant": "標題列"
  },
  "样本行数": {
    "en": "Sample rows",
    "zh-Hant": "樣本列數"
  },
  "样本预览": {
    "en": "Sample preview",
    "zh-Hant": "樣本預覽"
  },
  "正在上传与读取…": {
    "en": "Uploading and reading…",
    "zh-Hant": "正在上傳與讀取…"
  },
  "正在取消…": {
    "en": "Cancelling…",
    "zh-Hant": "正在取消…"
  },
  "步骤": {
    "en": "Step",
    "zh-Hant": "步驟"
  },
  "步骤引用了不存在的列": {
    "en": "Step refers to a missing column",
    "zh-Hant": "步驟參照了不存在的欄"
  },
  "每个来源使用同一行偏移和样本数量。关联与汇总只反映样本；正式运行处理整个输入范围。": {
    "en": "Each source uses the same row offset and sample size. Joins and aggregates reflect only samples; a full run processes the entire input range.",
    "zh-Hant": "每個來源使用相同列偏移和樣本數量。關聯與彙總只反映樣本；正式執行處理整個輸入範圍。"
  },
  "比较值（$名称引用参数）": {
    "en": "Comparison value ($name references a parameter)",
    "zh-Hant": "比較值（$名稱參照參數）"
  },
  "汇总指标": {
    "en": "Measures",
    "zh-Hant": "彙總指標"
  },
  "汇总方式": {
    "en": "Aggregation",
    "zh-Hant": "彙總方式"
  },
  "没有数据行": {
    "en": "No data rows",
    "zh-Hant": "沒有資料列"
  },
  "添加参数": {
    "en": "Add parameter",
    "zh-Hant": "新增參數"
  },
  "添加处理步骤": {
    "en": "Processing step type",
    "zh-Hant": "新增處理步驟"
  },
  "添加指标": {
    "en": "Add measure",
    "zh-Hant": "新增指標"
  },
  "添加步骤": {
    "en": "Add step",
    "zh-Hant": "新增步驟"
  },
  "添加步骤，或直接使用原始数据": {
    "en": "Add steps, or use the source data unchanged",
    "zh-Hant": "新增步驟，或直接使用原始資料"
  },
  "清理空白": {
    "en": "Trim whitespace",
    "zh-Hant": "清理空白"
  },
  "版本已停用或方案已归档": {
    "en": "Version retired or recipe archived",
    "zh-Hant": "版本已停用或方案已封存"
  },
  "生命周期管理": {
    "en": "Lifecycle settings",
    "zh-Hant": "生命週期管理"
  },
  "留空则拒绝没有时区的日期时间": {
    "en": "Leave blank to reject timestamps without a timezone",
    "zh-Hant": "留空則拒絕沒有時區的日期時間"
  },
  "目标类型": {
    "en": "Target type",
    "zh-Hant": "目標類型"
  },
  "空值": {
    "en": "Null",
    "zh-Hant": "空值"
  },
  "空值处理": {
    "en": "Null handling",
    "zh-Hant": "空值處理"
  },
  "空值按空字符串处理": {
    "en": "Treat nulls as empty strings",
    "zh-Hant": "空值依空字串處理"
  },
  "空值标记（每行一个）": {
    "en": "Null markers (one per line)",
    "zh-Hant": "空值標記（每行一個）"
  },
  "空字符串": {
    "en": "Empty string",
    "zh-Hant": "空字串"
  },
  "类型转换": {
    "en": "Convert type",
    "zh-Hant": "類型轉換"
  },
  "精度处理": {
    "en": "Precision policy",
    "zh-Hant": "精度處理"
  },
  "绑定本次输入，方案本身不保存业务数据。所有来源合计受处理规模限制。": {
    "en": "Bind inputs for this run; recipes do not store business data. Limits apply to all sources combined.",
    "zh-Hant": "綁定本次輸入，方案本身不儲存業務資料。所有來源合計受處理規模限制。"
  },
  "结束列编号（包含）": {
    "en": "Last column number (inclusive)",
    "zh-Hant": "結束欄編號（包含）"
  },
  "结束范围留空表示到最后一行或列；日期单元格转换为本地 ISO 日期时间，毫秒精度。数值格式不自动补前导零。": {
    "en": "Leave the end blank to include all remaining rows or columns. Date cells become local ISO timestamps at millisecond precision. Number formats do not add leading zeros.",
    "zh-Hant": "結束範圍留空表示到最後一列或欄；日期儲存格轉換為本地 ISO 日期時間，毫秒精度。數值格式不自動補前導零。"
  },
  "结束行（包含）": {
    "en": "Last row (inclusive)",
    "zh-Hant": "結束列（包含）"
  },
  "缺失": {
    "en": "Missing",
    "zh-Hant": "缺失"
  },
  "范围包含公式，请明确选择缓存值或调整范围": {
    "en": "Range contains formulas; explicitly choose cached values or change the range",
    "zh-Hant": "範圍包含公式，請明確選擇快取值或調整範圍"
  },
  "草稿修订 {n}": {
    "en": "Draft revision {n}",
    "zh-Hant": "草稿修訂 {n}"
  },
  "草稿尚未保存，离开会丢失修改。继续？": {
    "en": "Leave without saving your draft changes?",
    "zh-Hant": "草稿尚未儲存，離開會遺失修改。繼續？"
  },
  "行数": {
    "en": "Row count",
    "zh-Hant": "列數"
  },
  "表单提交": {
    "en": "Form submissions",
    "zh-Hant": "表單提交"
  },
  "要求列完全一致": {
    "en": "Require identical columns",
    "zh-Hant": "要求欄完全一致"
  },
  "要求唯一匹配": {
    "en": "Require unique match",
    "zh-Hant": "要求唯一相符"
  },
  "规则校验": {
    "en": "Validate rule",
    "zh-Hant": "規則驗證"
  },
  "该行列数超过标题范围": {
    "en": "Row has more columns than the header",
    "zh-Hant": "該列欄數超過標題範圍"
  },
  "请检查必填字段与步骤参数": {
    "en": "Check required fields and step parameters",
    "zh-Hant": "請檢查必填欄位與步驟參數"
  },
  "请绑定所有来源": {
    "en": "Bind all required sources",
    "zh-Hant": "請綁定所有來源"
  },
  "请选择 CSV 或 XLSX 文件": {
    "en": "Choose a CSV or XLSX file",
    "zh-Hant": "請選擇 CSV 或 XLSX 檔案"
  },
  "请选择列": {
    "en": "Choose a column",
    "zh-Hant": "請選擇欄"
  },
  "起始列编号": {
    "en": "First column number",
    "zh-Hant": "起始欄編號"
  },
  "跳过数据行数": {
    "en": "Data rows to skip",
    "zh-Hant": "跳過資料列數"
  },
  "转换会丢失小数精度": {
    "en": "Conversion would lose decimal precision",
    "zh-Hant": "轉換會遺失小數精度"
  },
  "载入更多数据集": {
    "en": "Load more datasets",
    "zh-Hant": "載入更多資料集"
  },
  "输入与运行": {
    "en": "Inputs and execution",
    "zh-Hant": "輸入與執行"
  },
  "输入校验失败": {
    "en": "Input integrity check failed",
    "zh-Hant": "輸入驗證失敗"
  },
  "输入行数": {
    "en": "Input rows",
    "zh-Hant": "輸入列數"
  },
  "输出 {n} 行 · 异常 {errors} 项": {
    "en": "{n} output rows · {errors} issues",
    "zh-Hant": "輸出 {n} 列 · 異常 {errors} 項"
  },
  "输出列名": {
    "en": "Output column name",
    "zh-Hant": "輸出欄名"
  },
  "输出列名重复": {
    "en": "Duplicate output column names",
    "zh-Hant": "輸出欄名重複"
  },
  "输出列（每行一个）": {
    "en": "Output columns (one per line)",
    "zh-Hant": "輸出欄（每行一個）"
  },
  "输出行数": {
    "en": "Output rows",
    "zh-Hant": "輸出列數"
  },
  "过滤": {
    "en": "Filter",
    "zh-Hant": "篩選"
  },
  "运行历史": {
    "en": "Run history",
    "zh-Hant": "執行歷程"
  },
  "运行参数": {
    "en": "Run parameters",
    "zh-Hant": "執行參數"
  },
  "发起这次运行的应用已停用，运行已停止": {
    "en": "The app that started this run has been disabled; the run was stopped",
    "zh-Hant": "發起這次執行的應用已停用，執行已停止"
  },
  "发起这次运行的应用授权已撤回，运行已停止": {
    "en": "Access for the app that started this run was withdrawn; the run was stopped",
    "zh-Hant": "發起這次執行的應用授權已撤回，執行已停止"
  },
  "发起这次运行的应用已不再持有所需权限，运行已停止": {
    "en": "The app that started this run no longer holds the permission it needs; the run was stopped",
    "zh-Hant": "發起這次執行的應用已不再持有所需權限，執行已停止"
  },
  "有 {n} 条记录的来源权限已改变，未在此显示。": {
    "en": "{n} record(s) are not shown because access to their sources has changed.",
    "zh-Hant": "有 {n} 筆記錄的來源權限已改變，未在此顯示。"
  },
  "运行多次中断，请检查后重试": {
    "en": "Run interrupted repeatedly; investigate before retrying",
    "zh-Hant": "執行多次中斷，請檢查後重試"
  },
  "运行尚未结束": {
    "en": "Run has not finished",
    "zh-Hant": "執行尚未結束"
  },
  "运行尝试次数：{n}": {
    "en": "Execution attempts: {n}",
    "zh-Hant": "執行嘗試次數：{n}"
  },
  "运行版本": {
    "en": "Run version",
    "zh-Hant": "執行版本"
  },
  "运行超时，请缩小范围": {
    "en": "Run timed out; reduce the input range",
    "zh-Hant": "執行逾時，請縮小範圍"
  },
  "这里还没有记录": {
    "en": "No records yet",
    "zh-Hant": "這裡還沒有記錄"
  },
  "追加或关联来源": {
    "en": "Append or join source",
    "zh-Hant": "追加或關聯來源"
  },
  "追加数据": {
    "en": "Append source",
    "zh-Hant": "追加資料"
  },
  "追加数据的列不一致": {
    "en": "Appended source columns do not match",
    "zh-Hant": "追加資料的欄不一致"
  },
  "选择列": {
    "en": "Select columns",
    "zh-Hant": "選擇欄"
  },
  "选择发布版本": {
    "en": "Choose a published version",
    "zh-Hant": "選擇發佈版本"
  },
  "选择数据集": {
    "en": "Choose a dataset",
    "zh-Hant": "選擇資料集"
  },
  "逐步统计": {
    "en": "Step statistics",
    "zh-Hant": "逐步統計"
  },
  "重复匹配": {
    "en": "Duplicate matches",
    "zh-Hant": "重複相符"
  },
  "重新载入": {
    "en": "Reload",
    "zh-Hant": "重新載入"
  },
  "重新载入会丢弃本地修改。继续？": {
    "en": "Reload and discard local changes?",
    "zh-Hant": "重新載入會捨棄本地修改。繼續？"
  },
  "银行家舍入": {
    "en": "Round half to even",
    "zh-Hant": "銀行家捨入"
  },
  "降序": {
    "en": "Descending",
    "zh-Hant": "遞減"
  },
  "预览中…": {
    "en": "Previewing…",
    "zh-Hant": "預覽中…"
  },
  "预览产出 {n} 行，最多显示 200 行": {
    "en": "Preview produced {n} rows; showing up to 200",
    "zh-Hant": "預覽產出 {n} 列，最多顯示 200 列"
  },
  "预览当前草稿": {
    "en": "Preview current draft",
    "zh-Hant": "預覽目前草稿"
  },
  "预览结果过大，请缩小样本": {
    "en": "Preview is too large; reduce the sample size",
    "zh-Hant": "預覽結果過大，請縮小樣本"
  }
} as const;

registerMessages(dataprep);
