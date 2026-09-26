import { registerMessages, type Translator } from '@synwevo/ui';

// Human contract prose only. Wire fields, paths, enums, scopes and schema titles stay literal.
// Keep this literal catalog testable against the real Docs OpenAPI surface.
export const contractEntries: [string, string, string][] = [
  [
    "Webhook 订阅详情（不含密钥）",
    "Webhook subscription details (without secrets)",
    "Webhook 訂閱詳情（不含金鑰）"
  ],
  [
    "bytes <start>-<end>/<size>；省略时视为整个文件（从 0 开始）",
    "bytes <start>-<end>/<size>; omit for the entire file starting at 0",
    "bytes <start>-<end>/<size>；省略時視為整個檔案（從 0 開始）"
  ],
  [
    "error 或 warning；省略为全部",
    "error or warning; omit for all levels",
    "error 或 warning；省略為全部"
  ],
  [
    "multipart 文件的 SHA-256（十六进制）；与收到的内容不一致时拒绝",
    "Hexadecimal SHA-256 of the multipart file; rejected if it differs from the received content",
    "multipart 檔案的 SHA-256（十六進位）；與收到的內容不一致時拒絕"
  ],
  [
    "original（上传的原文件）或 working（含字段的工作副本）",
    "original (uploaded file) or working (working copy with fields)",
    "original（上傳的原始檔案）或 working（含欄位的工作副本）"
  ],
  [
    "一条投递记录",
    "One delivery record",
    "一筆投遞記錄"
  ],
  [
    "上一页返回的 next_cursor（不透明，按稳定排序续读；与 offset 二选一）",
    "Opaque next_cursor from the previous page; continues stable ordering. Use either cursor or offset",
    "上一頁傳回的 next_cursor（不透明，按穩定排序續讀；與 offset 二選一）"
  ],
  [
    "上传来源文件（multipart、已完成的可续传上传 upload_id，或授权目录下的 path）",
    "Upload a source file (multipart, completed resumable upload_id, or path in an authorized directory)",
    "上傳來源檔案（multipart、已完成的可續傳上傳 upload_id，或授權目錄下的 path）"
  ],
  [
    "上传模板（multipart，或已完成的可续传上传 upload_id）",
    "Upload a template (multipart or completed resumable upload_id)",
    "上傳範本（multipart，或已完成的可續傳上傳 upload_id）"
  ],
  [
    "上传状态（已收到的字节数，用于断点续传）",
    "Upload status (bytes received for resuming)",
    "上傳狀態（已收到的位元組數，用於續傳）"
  ],
  [
    "下载一个产物",
    "Download an artifact",
    "下載一個產物"
  ],
  [
    "下载全部产物（ZIP，含生成清单）",
    "Download all artifacts (ZIP with generation manifest)",
    "下載全部產物（ZIP，含生成清單）"
  ],
  [
    "下载文件：API 密钥与服务账户只需 read；用户委托令牌还要求该成员在工作空间有导出权限（查看者没有导出权限时返回 403 export_not_allowed）。",
    "Downloads require read for API keys and service accounts. Delegated user tokens also require workspace export permission; viewers without it receive 403 export_not_allowed.",
    "下載檔案：API 金鑰與服務帳戶只需 read；使用者委託權杖還要求該成員在工作空間有匯出權限（檢視者沒有匯出權限時傳回 403 export_not_allowed）。"
  ],
  [
    "下载模板文件（原始或工作副本）",
    "Download a template file (original or working copy)",
    "下載範本檔案（原始或工作副本）"
  ],
  [
    "不带 simulate 只需 read 权限。simulate 会在请求内逐样本实际渲染，需要 write 权限，且最多覆盖 200 个样本（true 为前 50 个）；要覆盖全部样本请提交任务。",
    "Without simulate, only read is required. simulate renders actual samples within the request, requires write and covers at most 200 samples (true means the first 50). Submit a job to cover every sample.",
    "不帶 simulate 只需 read 權限。simulate 會在請求內逐樣本實際渲染，需要 write 權限，且最多涵蓋 200 個樣本（true 為前 50 個）；要涵蓋全部樣本請提交任務。"
  ],
  [
    "与界面预览共用模板版本、样本计划及渲染/读回校验。需要 write 与导出权限；不创建批次任务或正式产物。一次仅渲染一个样本，受模拟生成并发限制。响应是二进制附件；signature 可用预检返回值固定输入版本。",
    "Uses the same template version, sample plan, rendering and read-back validation as the UI preview. Requires write and export permission. Creates no batch job or persisted artifact. Renders one sample under simulation concurrency limits. Returns a binary attachment; use the preflight signature to pin input versions.",
    "與介面預覽共用範本版本、樣本計畫及渲染／讀回校驗。需要 write 與匯出權限；不建立批次任務或正式產物。一次僅渲染一個樣本，受模擬生成並行限制。回應是二進位附件；signature 可用預檢傳回值固定輸入版本。"
  ],
  [
    "产物清单（offset 或 cursor 分页）",
    "Artifact list (offset or cursor pagination)",
    "產物清單（offset 或 cursor 分頁）"
  ],
  [
    "从项目移除来源文件",
    "Remove a source file from the project",
    "從專案移除來源檔案"
  ],
  [
    "任何有效凭据都可以调用（不要求特定权限）。",
    "Any valid credential can call this endpoint; no specific scope is required.",
    "任何有效憑證都可以呼叫（不要求特定權限）。"
  ],
  [
    "任务事件（轮询用，seq 递增）",
    "Job events (polling with increasing seq)",
    "任務事件（輪詢用，seq 遞增）"
  ],
  [
    "任务状态与计数",
    "Job status and counts",
    "任務狀態與計數"
  ],
  [
    "任务的运行记录（初次与重试；offset 或 cursor 分页）",
    "Job runs (initial and retries; offset or cursor pagination)",
    "任務的執行記錄（初次與重試；offset 或 cursor 分頁）"
  ],
  [
    "保存映射（带并发校验）",
    "Save mappings with concurrency checks",
    "儲存映射（帶並行校驗）"
  ],
  [
    "保存样本规则",
    "Save the sample rule",
    "儲存樣本規則"
  ],
  [
    "保存编辑器模型为新版本；base_version 防止覆盖并发修改",
    "Save the editor model as a new version; base_version protects concurrent changes",
    "儲存編輯器模型為新版本；base_version 防止覆蓋並行修改"
  ],
  [
    "修改订阅（If-Match 或 version 防止覆盖他人修改）",
    "Update a subscription (If-Match or version protects concurrent changes)",
    "修改訂閱（If-Match 或 version 防止覆蓋他人修改）"
  ],
  [
    "写入一段字节（Content-Range: bytes 起-止/总长）",
    "Write a byte segment (Content-Range: bytes start-end/total)",
    "寫入一段位元組（Content-Range: bytes 起-止/總長）"
  ],
  [
    "创建 Webhook 订阅（密钥只在此返回一次）",
    "Create a Webhook subscription (secret returned only once)",
    "建立 Webhook 訂閱（金鑰只在此傳回一次）"
  ],
  [
    "创建可续传上传（大文件分段上传）",
    "Create a resumable upload for large files",
    "建立可續傳上傳（大檔案分段上傳）"
  ],
  [
    "创建项目（支持 Idempotency-Key）",
    "Create a project (supports Idempotency-Key)",
    "建立專案（支援 Idempotency-Key）"
  ],
  [
    "删除模板",
    "Delete a template",
    "刪除範本"
  ],
  [
    "删除订阅（未完成的重试随之停止）",
    "Delete a subscription and stop pending retries",
    "刪除訂閱（未完成的重試隨之停止）"
  ],
  [
    "删除项目",
    "Delete a project",
    "刪除專案"
  ],
  [
    "取消上传并删除已收到的字节",
    "Cancel an upload and delete received bytes",
    "取消上傳並刪除已收到的位元組"
  ],
  [
    "取消任务",
    "Cancel a job",
    "取消任務"
  ],
  [
    "只重试 failed；不重新执行 cancelled/skipped/未规划样本，不能同时 include_skipped。省略保持旧行为（failed 和 cancelled）",
    "Retry only failed items; exclude cancelled, skipped and unplanned samples. Cannot combine with include_skipped. Omitting this retains the previous behavior (failed and cancelled).",
    "只重試 failed；不重新執行 cancelled/skipped/未規劃樣本，不能同時 include_skipped。省略保持舊行為（failed 和 cancelled）"
  ],
  [
    "可选：整个文件的 SHA-256（十六进制），complete 时校验",
    "Optional hexadecimal SHA-256 of the entire file; verified at complete",
    "可選：整個檔案的 SHA-256（十六進位），complete 時校驗"
  ],
  [
    "固定使用的模板版本；省略或 null 表示始终使用最新版本",
    "Pin a template version; omit or use null to always use the latest",
    "固定使用的範本版本；省略或 null 表示始終使用最新版本"
  ],
  [
    "在内存中逐样本试生成并丢弃结果，报告只在生成时才会出现的问题；不写入任何产物。true 模拟前 50 个有效样本，正整数 N（最大 200）模拟前 N 个；响应的 simulation.limited 表示是否还有未模拟的样本。需要 write 权限",
    "Render samples in memory and discard results to detect generation-only issues; persist no artifacts. true simulates the first 50 valid samples; a positive N (up to 200) simulates the first N. simulation.limited indicates remaining unsimulated samples. Requires write.",
    "在記憶體中逐樣本試生成並丟棄結果，回報只在生成時才會出現的問題；不寫入任何產物。true 模擬前 50 個有效樣本，正整數 N（最大 200）模擬前 N 個；回應的 simulation.limited 表示是否還有未模擬的樣本。需要 write 權限"
  ],
  [
    "存活检查（进程是否在运行）",
    "Liveness check (is the process running)",
    "存活檢查（程序是否在執行）"
  ],
  [
    "完成上传：校验大小与 SHA-256 后保存",
    "Complete an upload after verifying size and SHA-256",
    "完成上傳：校驗大小與 SHA-256 後儲存"
  ],
  [
    "导入项目定义（全部校验通过才写入；可带 base_rev 做并发校验）",
    "Import a project definition atomically after validation; optional base_rev checks concurrency",
    "匯入專案定義（全部校驗通過才寫入；可帶 base_rev 做並行校驗）"
  ],
  [
    "导出字段定义",
    "Export field definitions",
    "匯出欄位定義"
  ],
  [
    "导出项目定义（模板绑定 + 规则 + 映射）",
    "Export a project definition (template binding, rule and mappings)",
    "匯出專案定義（範本綁定 + 規則 + 映射）"
  ],
  [
    "就绪检查（能否真正处理任务）",
    "Readiness check (can the service process work)",
    "就緒檢查（能否真正處理任務）"
  ],
  [
    "已 complete 的可续传上传（POST /uploads）",
    "A completed resumable upload (POST /uploads)",
    "已 complete 的可續傳上傳（POST /uploads）"
  ],
  [
    "已弃用：请改用 Idempotency-Key 请求头",
    "Deprecated: use the Idempotency-Key request header",
    "已棄用：請改用 Idempotency-Key 請求標頭"
  ],
  [
    "当前凭据的元数据（不含任何密钥）",
    "Current credential metadata (no secrets)",
    "目前憑證的中繼資料（不含任何金鑰）"
  ],
  [
    "当前凭据的租户与权限",
    "Current credential tenant and scopes",
    "目前憑證的租戶與權限"
  ],
  [
    "当前映射版本（mapping_rev）；不一致返回 409 stale",
    "Current mapping_rev; mismatches return 409 stale",
    "目前映射版本（mapping_rev）；不一致傳回 409 stale"
  ],
  [
    "恢复订阅（此后的事件才会投递）",
    "Resume a subscription (future events only)",
    "恢復訂閱（此後的事件才會投遞）"
  ],
  [
    "投递记录（游标分页，最新在前）",
    "Delivery records (cursor pagination, newest first)",
    "投遞記錄（游標分頁，最新在前）"
  ],
  [
    "按声明式定义创建字段（一次调用产生一个模板版本；全部成功或全部不变；每次最多 100 个）",
    "Create fields from declarations (one template version per call; atomic; up to 100 fields)",
    "按宣告式定義建立欄位（一次呼叫產生一個範本版本；全部成功或全部不變；每次最多 100 個）"
  ],
  [
    "按签名链接下载（链接本身即凭据）",
    "Download using a signed link (the link itself is the credential)",
    "按簽章連結下載（連結本身即憑證）"
  ],
  [
    "提交生成任务（支持 Idempotency-Key）",
    "Submit a generation job (supports Idempotency-Key)",
    "提交生成任務（支援 Idempotency-Key）"
  ],
  [
    "撤销当前凭据（立即生效，不可恢复）",
    "Revoke the current credential immediately and irreversibly",
    "撤銷目前憑證（立即生效，不可恢復）"
  ],
  [
    "文件字节数，不超过文件大小上限",
    "File size in bytes, within the file size limit",
    "檔案位元組數，不超過檔案大小上限"
  ],
  [
    "无需鉴权。200 `ready` / 503 `not_ready`，带 `checks` 组件级状态；不含版本、schema 版本等诊断信息。",
    "No authentication. Returns 200 ready or 503 not_ready with component-level checks, excluding version and schema diagnostics.",
    "無需鑑權。200 `ready` / 503 `not_ready`，帶 `checks` 元件級狀態；不含版本、schema 版本等診斷資訊。"
  ],
  [
    "无需鉴权。返回 `{\"status\":\"ok\"}`；仅测试实例额外返回 `instance`。",
    "No authentication. Returns {\"status\":\"ok\"}; only test instances additionally return instance.",
    "無需鑑權。傳回 `{\"status\":\"ok\"}`；僅測試實例額外傳回 `instance`。"
  ],
  [
    "暂停订阅（排队中的投递不再发送）",
    "Pause a subscription (queued deliveries stop)",
    "暫停訂閱（排隊中的投遞不再傳送）"
  ],
  [
    "更换签名密钥（可设重叠期，期间两个签名并存）",
    "Rotate a signing secret (optional overlap keeps both signatures)",
    "更換簽章金鑰（可設重疊期，期間兩個簽章並存）"
  ],
  [
    "服务器上的绝对路径，须在 SYNWEVO_DOCS_IMPORT_ROOTS 授权目录内",
    "Absolute server path within an authorized SYNWEVO_DOCS_IMPORT_ROOTS directory",
    "伺服器上的絕對路徑，須在 SYNWEVO_DOCS_IMPORT_ROOTS 授權目錄內"
  ],
  [
    "本工作空间的 Webhook 订阅",
    "Webhook subscriptions in this workspace",
    "本工作空間的 Webhook 訂閱"
  ],
  [
    "本工作空间的事件流（游标分页；id 与 Webhook 的 webhook-id 相同）",
    "Workspace event stream (cursor pagination; id matches webhook-id)",
    "本工作空間的事件流（游標分頁；id 與 Webhook 的 webhook-id 相同）"
  ],
  [
    "本工作空间的任务用量与上限",
    "Workspace job usage and limits",
    "本工作空間的任務用量與上限"
  ],
  [
    "本部署真实可用的格式与依赖",
    "Formats and dependencies actually available in this deployment",
    "本部署實際可用的格式與相依套件"
  ],
  [
    "来源文件状态与结构",
    "Source file status and structure",
    "來源檔案狀態與結構"
  ],
  [
    "模板列表（按创建时间倒序；offset 或 cursor 分页）",
    "Templates (newest first; offset or cursor pagination)",
    "範本清單（按建立時間倒序；offset 或 cursor 分頁）"
  ],
  [
    "模板详情与版本",
    "Template details and versions",
    "範本詳情與版本"
  ],
  [
    "每页条数，默认 100，最大 1000",
    "Items per page; default 100, maximum 1000",
    "每頁筆數，預設 100，最大 1000"
  ],
  [
    "每页条数，默认 200，最大 1000",
    "Items per page; default 200, maximum 1000",
    "每頁筆數，預設 200，最大 1000"
  ],
  [
    "每页条数，默认 500，最大 2000",
    "Items per page; default 500, maximum 2000",
    "每頁筆數，預設 500，最大 2000"
  ],
  [
    "每页条数，默认 50，最大 200",
    "Items per page; default 50, maximum 200",
    "每頁筆數，預設 50，最大 200"
  ],
  [
    "生成 ZIP 的有时效下载链接",
    "Create an expiring ZIP download link",
    "產生 ZIP 的限時下載連結"
  ],
  [
    "生成并下载一个样本的真实原生格式预览",
    "Render and download one sample preview in its native format",
    "生成並下載一個樣本的真實原生格式預覽"
  ],
  [
    "生成有时效的下载链接（无需密钥即可下载；签发者失去权限后链接随之失效）",
    "Create an expiring download link (no key needed; loses access when its issuer does)",
    "產生限時下載連結（無需金鑰即可下載；簽發者失去權限後連結隨之失效）"
  ],
  [
    "生成清单（CSV）",
    "Generation manifest (CSV)",
    "生成清單（CSV）"
  ],
  [
    "用结构化编辑器模型创建 DOCX/XLSX 模板及字段",
    "Create a DOCX/XLSX template and fields from an editor model",
    "用結構化編輯器模型建立 DOCX/XLSX 範本及欄位"
  ],
  [
    "立即发送一次签名的 webhook.ping（只尝试一次）",
    "Send one signed webhook.ping immediately (one attempt only)",
    "立即傳送一次簽章的 webhook.ping（只嘗試一次）"
  ],
  [
    "绑定模板（可用 version 固定模板版本）",
    "Bind a template (optionally pin version)",
    "綁定範本（可用 version 固定範本版本）"
  ],
  [
    "读取指定版本的可移植编辑器模型及字段",
    "Read a versioned portable editor model and fields",
    "讀取指定版本的可攜式編輯器模型及欄位"
  ],
  [
    "读取映射",
    "Read mappings",
    "讀取映射"
  ],
  [
    "跳过的条数（与 cursor 二选一）",
    "Number of items to skip; use either offset or cursor",
    "略過的筆數（與 cursor 二選一）"
  ],
  [
    "轮换当前 API 密钥（新密钥只返回这一次）",
    "Rotate the current API key (new secret returned once)",
    "輪換目前 API 金鑰（新金鑰只傳回這一次）"
  ],
  [
    "逐样本结果（可按状态过滤；offset 或 cursor 分页）",
    "Per-sample results (filter by status; offset or cursor pagination)",
    "逐樣本結果（可按狀態篩選；offset 或 cursor 分頁）"
  ],
  [
    "重新投递（新记录，webhook-id 不变；限 7 天内的事件）",
    "Redeliver (new record, same webhook-id; events from the last 7 days only)",
    "重新投遞（新記錄，webhook-id 不變；限 7 天內的事件）"
  ],
  [
    "重试失败项（沿用原快照，可带 Idempotency-Key）",
    "Retry failed items (original snapshot; supports Idempotency-Key)",
    "重試失敗項（沿用原快照，可帶 Idempotency-Key）"
  ],
  [
    "项目列表（按创建时间倒序；offset 或 cursor 分页）",
    "Projects (newest first; offset or cursor pagination)",
    "專案清單（按建立時間倒序；offset 或 cursor 分頁）"
  ],
  [
    "项目的任务列表（按创建时间倒序；offset 或 cursor 分页）",
    "Project jobs (newest first; offset or cursor pagination)",
    "專案的任務清單（按建立時間倒序；offset 或 cursor 分頁）"
  ],
  [
    "项目的来源文件（按上传顺序；offset 或 cursor 分页）",
    "Project source files (upload order; offset or cursor pagination)",
    "專案的來源檔案（按上傳順序；offset 或 cursor 分頁）"
  ],
  [
    "项目详情（来源、模板、规则、映射）",
    "Project details (sources, template, rule and mappings)",
    "專案詳情（來源、範本、規則、映射）"
  ],
  [
    "预检问题明细（逐样本、逐字段，分页）",
    "Preflight issues by sample and field (paginated)",
    "預檢問題明細（逐樣本、逐欄位，分頁）"
  ],
  [
    "预检：样本数、有效数、预计输出、错误与警告（simulate 时实际试生成）",
    "Preflight: samples, valid count, expected outputs, errors and warnings (actual rendering with simulate)",
    "預檢：樣本數、有效數、預計輸出、錯誤與警告（simulate 時實際試生成）"
  ],
  [
    "请求成功",
    "Successful Response",
    "請求成功"
  ],
  [
    "通过现有浏览服务查看来源；offset/limit 仅分页表格行，其他格式返回原生文档视图。",
    "Browser service view. offset/limit window grid rows only; other formats return their native document view.",
    "透過既有瀏覽服務檢視來源；offset/limit 僅分頁表格列，其他格式傳回原生文件檢視。"
  ],
  [
    "从原生来源选择生成结构化定位器建议，使用现有浏览服务与当前项目规则；不保存映射、不执行 OCR。坐标与索引从零开始；PDF/图片 nrect 是视觉归一化的 [x0,y0,x1,y1]。",
    "Build structured locator proposals from a native source selection. Uses the browser service and current project rule; does not save a mapping or execute OCR. Coordinates and indices are zero-based; PDF/image nrect is visual normalized [x0,y0,x1,y1].",
    "從原生來源選取產生結構化定位器建議，使用既有瀏覽服務與目前專案規則；不儲存映射、不執行 OCR。座標與索引從零開始；PDF/圖片 nrect 是視覺正規化的 [x0,y0,x1,y1]。"
  ],
  [
    "仅适用 CSV/TSV。替换 csv 选项组（省略项恢复自动/默认值），通过共用服务同步重新解析；请检查返回的 status/error。",
    "CSV/TSV only. Replaces the csv options group (omitted controls reset to auto/default), synchronously reparses through the shared service. Check returned status/error.",
    "僅適用 CSV/TSV。取代 csv 選項群組（省略項恢復自動／預設值），透過共用服務同步重新解析；請檢查傳回的 status/error。"
  ],
  [
    "通过共用拆分器计算当前各区块的规则结果并分页；此接口不保存区块状态。",
    "Current per-block rule results, paged after the shared splitter runs. No block state is stored by this endpoint.",
    "透過共用拆分器計算目前各區塊的規則結果並分頁；此介面不儲存區塊狀態。"
  ],
  [
    "来自浏览器规则引擎的当前样本记录。共用服务先计算全部记录，再分页摘要；不创建任务或重复的记录状态。",
    "Current sample records from the browser's rule engine. Pages the summary; the shared service computes all records first. No job or duplicate record state is created.",
    "來自瀏覽器規則引擎的目前樣本記錄。共用服務先計算全部記錄，再分頁摘要；不建立任務或重複的記錄狀態。"
  ],
  [
    "按共用查看器顺序搜索字面文本。扫描来源以计算全部匹配数，最多保留 offset+limit 条；offset 上限为 10000。",
    "Literal text search in the shared viewer order. The service scans the source to count all matches; retains at most offset+limit hits. Offset is limited to 10000.",
    "按共用檢視器順序搜尋字面文字。掃描來源以計算全部匹配數，最多保留 offset+limit 筆；offset 上限為 10000。"
  ],
  [
    "返回原始上传字节、安全的附件文件名、no-store 和 X-Content-SHA256；Content-Type 反映原始格式。",
    "Original upload bytes, with safe attachment filename, no-store and X-Content-SHA256. Content-Type reflects the original file format.",
    "傳回原始上傳位元組、安全的附件檔案名稱、no-store 和 X-Content-SHA256；Content-Type 反映原始格式。"
  ],
  [
    "将当前已保存映射解析为本项目某个来源中的位置；只读。",
    "Resolve current saved mappings to positions in one source belonging to this project; read only.",
    "將目前已儲存映射解析為本專案某個來源中的位置；唯讀。"
  ],
  [
    "选择现有有效来源作为项目代表来源；不创建对象。",
    "Select an existing live source as the project's representative; creates no object.",
    "選取既有有效來源作為專案代表來源；不建立物件。"
  ],
  [
    "为来源所在项目当前版本的映射设置或清除（value=null）修正值，返回全部当前修正。",
    "Set or clear (value=null) a correction for a mapping in the source project's current revision. Returns all current corrections.",
    "為來源所在專案目前版本的映射設定或清除（value=null）修正值，傳回全部目前修正。"
  ],
  [
    "通过浏览器引擎渲染从零开始的 PDF/图片页。PDF 缩放受像素预算限制，图片返回原始尺寸；不保存产物，需要当前导出权限。",
    "Zero-based PDF/image page, rendered by the browser engine. PDF zoom is bounded by the pixel budget; image sources return native dimensions. No stored artifact; current export permission is required.",
    "透過瀏覽器引擎渲染從零開始的 PDF/圖片頁。PDF 縮放受像素預算限制，圖片傳回原始尺寸；不儲存產物，需要目前匯出權限。"
  ],
  [
    "从零开始的页及可选区块；分页现有文本层或已保存的 OCR 词，不执行 OCR。",
    "Zero-based page and optional block. Pages the existing text layer or previously stored OCR words; does not execute OCR.",
    "從零開始的頁及可選區塊；分頁既有文字層或已儲存的 OCR 詞，不執行 OCR。"
  ],
  [
    "ids 必须是本项目全部有效来源 ID 的完整排列。写入前校验所有 ID；按新顺序返回来源。",
    "ids must be a complete permutation of this project's live source ids. Validates all ids before writing; returns sources in their new order.",
    "ids 必須是本專案全部有效來源 ID 的完整排列。寫入前校驗所有 ID；按新順序傳回來源。"
  ],
  [
    "来源区块",
    "Blocks",
    "來源區塊"
  ],
  [
    "原始内容",
    "Content",
    "原始內容"
  ],
  [
    "修正值",
    "Correction",
    "修正值"
  ],
  [
    "定位映射",
    "Locate",
    "定位映射"
  ],
  [
    "定位器建议",
    "Locator",
    "定位器建議"
  ],
  [
    "解析选项",
    "Options",
    "解析選項"
  ],
  [
    "来源顺序",
    "Order",
    "來源順序"
  ],
  [
    "页面 PNG",
    "Page Png",
    "頁面 PNG"
  ],
  [
    "样本记录",
    "Records",
    "樣本記錄"
  ],
  [
    "代表来源",
    "Representative",
    "代表來源"
  ],
  [
    "文本搜索",
    "Search",
    "文字搜尋"
  ],
  [
    "来源结构",
    "Structure",
    "來源結構"
  ],
  [
    "来源视图",
    "View",
    "來源檢視"
  ],
  [
    "文本词",
    "Words",
    "文字詞"
  ],
  [
    "组织共享模板列表（当前空间所属组织；offset 或 cursor 分页；需委托令牌）",
    "Organization-shared templates (the current workspace's organization; offset or cursor paging; delegated token required)",
    "組織共享範本列表（目前空間所屬組織；offset 或 cursor 分頁；需委託權杖）"
  ],
  [
    "组织共享模板详情与发布历史（需委托令牌）",
    "A shared template with its release history (delegated token required)",
    "組織共享範本詳情與發布歷史（需委託權杖）"
  ],
  [
    "把模板发布到组织（冻结 head 版本为第 1 次发布；需委托令牌；支持 Idempotency-Key）",
    "Publish a template to the organization (the head version is frozen as release 1; delegated token required; Idempotency-Key supported)",
    "把範本發布到組織（凍結 head 版本為第 1 次發布；需委託權杖；支援 Idempotency-Key）"
  ],
  [
    "更新发布：源模板 head 有新版本时产生新一次发布，或只改说明（需委托令牌）",
    "New release: a new release when the source template's head moved, or a description change only (delegated token required)",
    "更新發布：來源範本 head 有新版本時產生新一次發布，或只改說明（需委託權杖）"
  ],
  [
    "弃用或恢复组织共享模板（需委托令牌）",
    "Deprecate or restore a shared template (delegated token required)",
    "棄用或恢復組織共享範本（需委託權杖）"
  ],
  [
    "把当前发布复制成本空间的模板，可同时绑定项目（需委托令牌；支持 Idempotency-Key）",
    "Copy the current release into this workspace as a template, optionally binding a project (delegated token required; Idempotency-Key supported)",
    "把目前發布複製成本空間的範本，可同時繫結專案（需委託權杖；支援 Idempotency-Key）"
  ],
  [
    "使用此模板的项目：跟随当前版本的与固定了版本的",
    "Projects using this template: those following the current version and those pinned to a version",
    "使用此範本的專案：跟隨目前版本的與固定了版本的"
  ],
  [
    "这个版本的说明（命名检查点）；留空时为「在编辑器中保存」",
    "This version's note (a named checkpoint); when empty, \"saved in the editor\"",
    "這個版本的說明（命名檢查點）；留空時為「在編輯器中儲存」"
  ],
  [
    "恢复某个版本：以它的内容新建当前版本（历史保留；base_head 不符则 409 stale_template；编辑器里有未保存为版本的实时修改则 409 live_edits，除非 discard_live=true）",
    "Restore a version: its content becomes a new current version (history kept; a mismatched base_head is 409 stale_template; live edits in the editing room not yet saved as a version are 409 live_edits unless discard_live=true)",
    "恢復某個版本：以它的內容新建目前版本（歷史保留；base_head 不符則 409 stale_template；編輯器裡有未儲存為版本的即時修改則 409 live_edits，除非 discard_live=true）"
  ]
];
registerMessages(Object.fromEntries(contractEntries.map(([source, en, hant]) => [source, { en, 'zh-Hant': hant }])));
const canonical = new Map(contractEntries.map(([source, en]) => [en, source]));

export function contractText(source: string, t: Translator): string {
  return source.split('\n\n').map(part => t(canonical.get(part) || part)).join('\n\n');
}

/** Translate only prose values, retaining the complete machine contract structure. */
export function localizeContract(value: unknown, t: Translator): unknown {
  if (Array.isArray(value)) return value.map(item => localizeContract(item, t));
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) =>
    [key, (key === 'description' || key === 'summary') && typeof item === 'string' ? contractText(item, t) : localizeContract(item, t)]));
  return value;
}
