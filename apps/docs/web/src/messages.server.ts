import type { Messages } from "@synwevo/ui/i18n-core";

/** Presentation catalog for server-owned interface descriptions, not document content. */
export const serverMessages = {
  '内置公式引擎已计算公式并写入结果。': { en: 'The built-in formula engine calculated formulas and saved the results.', 'zh-Hant': '內建公式引擎已計算公式並寫入結果。' },
  "部分公式无法计算，无法导出 CSV。请改用 XLSX，或请管理员启用排版引擎。": {
    "en": "Some formulas could not be calculated, so CSV export is unavailable. Use XLSX, or ask an administrator to enable the layout engine.",
    "zh-Hant": "部分公式無法計算，無法匯出 CSV。請改用 XLSX，或請管理員啟用排版引擎。"
  },
  '处理提示（{code}）。': { en: 'Processing notice ({code}).', 'zh-Hant': '處理提示（{code}）。' },
  '第 {start}–{end} 页': { en: 'Pages {start}–{end}', 'zh-Hant': '第 {start}–{end} 頁' },
  '第 {start}–{end} 行': { en: 'Rows {start}–{end}', 'zh-Hant': '第 {start}–{end} 行' },
  '第 {start}–{end} 个段落/表格': { en: 'Paragraphs/tables {start}–{end}', 'zh-Hant': '第 {start}–{end} 個段落/表格' },
  "映射已被修改": {"en": "Mappings have changed", "zh-Hant": "映射已被修改"},
  "可选字段为空，已跳过。": {"en": "An optional field is empty and was skipped.", "zh-Hant": "可選欄位為空，已跳過。"},
  "部分值未能应用指定的转换，请检查映射。": {"en": "Some values could not use the specified transform. Check the mappings.", "zh-Hant": "部分值未能套用指定的轉換，請檢查映射。"},
  "CSV 仅导出第一个工作表。": {"en": "CSV exports only the first worksheet.", "zh-Hant": "CSV 僅匯出第一個工作表。"},
  "内容超出幻灯片的目标区域。": {"en": "Content exceeds the target area on the slide.", "zh-Hant": "內容超出投影片的目標區域。"},
  "明细行已缩小以适应目标区域。": {"en": "Detail rows were reduced to fit the target area.", "zh-Hant": "明細行已縮小以適應目標區域。"},
  "明细已续排到新增页面。": {"en": "Details continue on additional pages.", "zh-Hant": "明細已續排到新增頁面。"},
  "已移除不支持的控制字符。": {"en": "Unsupported control characters were removed.", "zh-Hant": "已移除不支援的控制字元。"},
  "字段引用已被移除，请检查映射。": {"en": "A field reference was removed. Check the mappings.", "zh-Hant": "欄位引用已被移除，請檢查映射。"},
  "公式已交由排版引擎计算。": {
    "en": "Formulas were calculated by the layout engine.",
    "zh-Hant": "公式已交由排版引擎計算。"
  },
  "部分公式无法在服务器计算，请在 Excel 中检查。": {"en": "Some formulas cannot be calculated on the server. Check them in Excel.", "zh-Hant": "部分公式無法在伺服器計算，請在 Excel 中檢查。"},
  "部分公式的计算结果是错误值。": {"en": "Some formulas evaluated to error values.", "zh-Hant": "部分公式的計算結果是錯誤值。"},
  "公式未能重新计算，Excel 打开时会再次计算。": {"en": "Formulas could not be recalculated. Excel will recalculate on opening.", "zh-Hant": "公式未能重新計算，Excel 開啟時會再次計算。"},
  "表格处理存在限制，请检查模板与输出。": {"en": "Spreadsheet processing has limitations. Check the template and output.", "zh-Hant": "表格處理存在限制，請檢查模板與輸出。"},
  "{count} 行": {"en": "{count} rows", "zh-Hant": "{count} 行"},
  "第 {count} 次": {"en": "Attempt {count}", "zh-Hant": "第 {count} 次"},
  "第 {start}–{end} 条，共 {total} 条": {"en": "{start}–{end} of {total} items", "zh-Hant": "第 {start}–{end} 條，共 {total} 條"},
  "第 {start}–{end} 行各生成一份（表头为第 {header} 行）；将打开样本规则确认。": {"en": "Generate one document for each row {start}–{end} (header row: {header}); sample rules will open for confirmation.", "zh-Hant": "第 {start}–{end} 行各生成一份（表頭為第 {header} 行）；將開啟樣本規則確認。"},

  '正在下载…': { en: 'Downloading…', 'zh-Hant': '正在下載…' },
  '图片加载失败': { en: 'Image could not be loaded', 'zh-Hant': '圖片載入失敗' },

  "AGPL-3.0 或 Artifex 商业许可": {
    "en": "AGPL-3.0 or an Artifex commercial license",
    "zh-Hant": "AGPL-3.0 或 Artifex 商業許可"
  },
  "引擎 Apache-2.0 / BSD-3-Clause；但本部署仍安装了 PyMuPDF（AGPL-3.0），义务仍适用": {
    "en": "Engine: Apache-2.0 / BSD-3-Clause; PyMuPDF (AGPL-3.0) is still installed, so its obligations still apply",
    "zh-Hant": "引擎 Apache-2.0 / BSD-3-Clause；但本部署仍安裝了 PyMuPDF（AGPL-3.0），義務仍適用"
  },
  "已实现并有自动化测试": {
    "en": "Implemented and covered by automated tests",
    "zh-Hant": "已實現並有自動化測試"
  },
  "经格式转换（可能有损，已提示）": {
    "en": "Converted (possible losses are disclosed)",
    "zh-Hant": "經格式轉換（可能有損，已提示）"
  },
  "有限支持（见说明）": {
    "en": "Limited support (see details)",
    "zh-Hant": "有限支持（見說明）"
  },
  "功能已实现但本机缺少依赖或当前部署未启用": {
    "en": "Implemented, but a dependency is missing or disabled in this deployment",
    "zh-Hant": "功能已實現但本機缺少依賴或當前部署未啓用"
  },
  "不支持": {
    "en": "Unsupported",
    "zh-Hant": "不支持"
  },
  "图片模板（PNG/JPEG）可用：在像素上直接清除与写入，支持中文、换行与缩放；扫描型 PDF 模板仍需 PDF 引擎完整模式。": {
    "en": "PNG/JPEG templates support direct pixel removal and writing, Chinese text, wrapping and scaling. Scanned PDF templates still require the PDF engine's full mode.",
    "zh-Hant": "圖片模板（PNG/JPEG）可用：在像素上直接清除與寫入，支持中文、換行與縮放；掃描型 PDF 模板仍需 PDF 引擎完整模式。"
  },
  "服务端本地（PDF 引擎基础模式） + 本地文字识别": {
    "en": "Local server (PDF engine, basic mode) + local text recognition",
    "zh-Hant": "服務端本地（PDF 引擎基礎模式） + 本機文字辨識"
  },
  "服务端本地（PDF 引擎基础模式）": {
    "en": "Local server (PDF engine, basic mode)",
    "zh-Hant": "服務端本地（PDF 引擎基礎模式）"
  },
  "图片模板输出同尺寸 PNG/JPEG；扫描型 PDF 无原生输出": {
    "en": "Image templates produce same-size PNG/JPEG; scanned PDF templates have no native output",
    "zh-Hant": "圖片模板輸出同尺寸 PNG/JPEG；掃描型 PDF 無原生輸出"
  },
  "PDF 模板不可用，因此没有原生输出": {
    "en": "PDF templates are unavailable, so no native output is available",
    "zh-Hant": "PDF 模板不可用，因此沒有原生輸出"
  },
  "内置公式引擎（90+ 常用函数）计算并写入结果，其他软件读取也能看到正确值；不支持的函数自动改由排版引擎计算，Excel 打开时仍会重算。XLSM 输出扩展名为 .xlsx，宏不执行": {
    "en": "The built-in engine calculates and stores results for 90+ common functions, readable by other software. Unsupported functions fall back to the layout engine, and Excel recalculates on open. XLSM outputs use the .xlsx extension; macros do not run",
    "zh-Hant": "內置公式引擎（90+ 常用函數）計算並寫入結果，其他軟件讀取也能看到正確值；不支持的函數自動改由排版引擎計算，Excel 打開時仍會重算。XLSM 輸出擴展名為 .xlsx，宏不執行"
  },
  "服务端本地": {
    "en": "Local server",
    "zh-Hant": "服務端本地"
  },
  "支持批量、失败重试、重新打开校验": {
    "en": "Batch generation, failed-item retry and reopening for verification",
    "zh-Hant": "支持批量、失敗重試、重新打開校驗"
  },
  "只保存值；导出 CSV 时写出公式的计算结果": {
    "en": "Values only; CSV export writes calculated formula results",
    "zh-Hant": "只保存值；導出 CSV 時寫出公式的計算結果"
  },
  "支持": {
    "en": "Supported",
    "zh-Hant": "支持"
  },
  "转换可能改变部分格式；宏不执行": {
    "en": "Conversion may alter formatting; macros do not run",
    "zh-Hant": "轉換可能改變部分格式；宏不執行"
  },
  "服务端本地 + 排版引擎": {
    "en": "Local server + layout engine",
    "zh-Hant": "服務端本地 + 排版引擎"
  },
  "转换可能损失部分格式": {
    "en": "Conversion may lose some formatting",
    "zh-Hant": "轉換可能損失部分格式"
  },
  "新内容更长时分页可能变化；不承诺像素级不变。DOCM 输出为 .docx": {
    "en": "Longer content may change pagination; pixel-identical output is not guaranteed. DOCM exports as .docx.",
    "zh-Hant": "新內容更長時分頁可能變化；不承諾像素級不變。DOCM 輸出為 .docx"
  },
  "转换可能改变版式、丢失宏与嵌入对象": {
    "en": "Conversion may change layout and lose macros or embedded objects",
    "zh-Hant": "轉換可能改變版式、丟失宏與嵌入對象"
  },
  "PDF（文本型）": {
    "en": "PDF (text)",
    "zh-Hant": "PDF（文本型）"
  },
  "只替换框内文字；不能恢复为可编辑 Word；明细区域行高固定（单元格内可换行缩字，不会自动加高行）": {
    "en": "Only text within boxes is replaced; cannot reconstruct editable Word files. Detail row height is fixed; cells may wrap or shrink but rows do not grow automatically.",
    "zh-Hant": "只替換框內文字；不能恢復為可編輯 Word；明細區域行高固定（單元格內可換行縮字，不會自動加高行）"
  },
  "服务端本地（PDF 引擎完整模式）": {
    "en": "Local server (PDF engine, full mode)",
    "zh-Hant": "服務端本地（PDF 引擎完整模式）"
  },
  "PDF（表单型 AcroForm）": {
    "en": "PDF (AcroForm)",
    "zh-Hant": "PDF（表單型 AcroForm）"
  },
  "输出表单为扁平化文字，不保留可编辑字段": {
    "en": "Form fields are flattened to text and no longer editable",
    "zh-Hant": "輸出表單為扁平化文字，不保留可編輯字段"
  },
  "PDF（扫描型）/ PNG / JPEG / TIFF / BMP / WEBP": {
    "en": "PDF (scanned) / PNG / JPEG / TIFF / BMP / WEBP",
    "zh-Hant": "PDF（掃描型）/ PNG / JPEG / TIFF / BMP / WEBP"
  },
  "OCR 结果用于取值，不会用 OCR 重建整份文件；JPEG 输出重新压缩（质量 95）；图片模板只有一页，明细超出时只能报错或压缩行高": {
    "en": "OCR supplies values, without rebuilding the full file. JPEG output is recompressed at quality 95. Image templates have one page; overflowing details must fail or compress row height.",
    "zh-Hant": "OCR 結果用於取值，不會用 OCR 重建整份文件；JPEG 輸出重新壓縮（質量 95）；圖片模板只有一頁，明細超出時只能報錯或壓縮行高"
  },
  "服务端本地（本地文字识别）": {
    "en": "Local server (local text recognition)",
    "zh-Hant": "服務端本地（本機文字辨識）"
  },
  "不套用 Word 分页规则；动画/母版不受影响": {
    "en": "Word pagination rules do not apply; animations and masters are unaffected",
    "zh-Hant": "不套用 Word 分頁規則；動畫/母版不受影響"
  },
  "转换可能丢失动画与嵌入对象": {
    "en": "Conversion may lose animations and embedded objects",
    "zh-Hant": "轉換可能丟失動畫與嵌入對象"
  },
  "服务端本地（隔离：不渲染原始 HTML）": {
    "en": "Local server (isolated; original HTML is not rendered)",
    "zh-Hant": "服務端本地（隔離：不渲染原始 HTML）"
  },
  "读取单元格值、类型、数字格式显示、合并单元格；公式读取文件内计算结果；没有结果的公式由内置公式引擎计算": {
    "en": "Reads values, types, formatted numbers and merged cells. Uses cached formula results; calculates uncached formulas with the built-in engine.",
    "zh-Hant": "讀取單元格值、類型、數字格式顯示、合併單元格；公式讀取文件內計算結果；沒有結果的公式由內置公式引擎計算"
  },
  "完整表格网格（分页加载全部行），工作表切换，单元格/区域框选": {
    "en": "Full grid with all rows loaded in pages, worksheet switching and cell/range selection",
    "zh-Hant": "完整表格網格（分頁加載全部行），工作表切換，單元格/區域框選"
  },
  "单元格、固定区域、重复明细行；工作副本以隐藏定义名称标记字段": {
    "en": "Cells, fixed regions and repeating detail rows; hidden defined names mark fields in working copies",
    "zh-Hant": "單元格、固定區域、重復明細行；工作副本以隱藏定義名稱標記字段"
  },
  "单值、固定区域、重复明细行（插入行时平移公式、合并、条件格式、数据验证、图表范围）": {
    "en": "Single values, fixed regions and repeating detail rows; inserted rows shift formulas, merges, conditional formatting, validation and chart ranges",
    "zh-Hant": "單值、固定區域、重復明細行（插入行時平移公式、合併、條件格式、數據驗證、圖表範圍）"
  },
  "直接改写 XML，未修改部分原样保留（图表、图片、透视表等部件不被重写）": {
    "en": "Edits XML directly; untouched parts, including charts, images and pivot tables, are retained",
    "zh-Hant": "直接改寫 XML，未修改部分原樣保留（圖表、圖片、透視表等部件不被重寫）"
  },
  "经排版引擎转换": {
    "en": "Converted by the layout engine",
    "zh-Hant": "經排版引擎轉換"
  },
  "RFC 4180 解析；自动识别 UTF-8/GB18030/UTF-16 编码与分隔符，可手动修正；单元格内换行不拆行；全部按文本保留（前导零、长数字不变）": {
    "en": "RFC 4180 parsing; automatic UTF-8/GB18030/UTF-16 and delimiter detection with manual overrides. Multiline cells stay intact; text preserves leading zeros and long numbers.",
    "zh-Hant": "RFC 4180 解析；自動識別 UTF-8/GB18030/UTF-16 編碼與分隔符，可手動修正；單元格內換行不拆行；全部按文本保留（前導零、長數字不變）"
  },
  "表格网格": {
    "en": "Spreadsheet grid",
    "zh-Hant": "表格網格"
  },
  "CSV 无样式/合并/多工作表，不能作为排版模板；需要排版请用 XLSX 或文档模板": {
    "en": "CSV has no styling, merges or multiple sheets and cannot serve as a layout template. Use XLSX or a document template.",
    "zh-Hant": "CSV 無樣式/合併/多工作表，不能作為排版模板；需要排版請用 XLSX 或文檔模板"
  },
  "可由表格型模板导出 CSV（仅第一个工作表的值）": {
    "en": "Spreadsheet templates can export CSV (values from the first worksheet only)",
    "zh-Hant": "可由表格型模板導出 CSV（僅第一個工作表的值）"
  },
  "排版引擎可用时转换为 XLSX 读取；否则用内置读取器直接读取值与数字格式": {
    "en": "Converts to XLSX for reading when the layout engine is available; otherwise the built-in reader reads values and number formats directly",
    "zh-Hant": "排版引擎可用時轉換為 XLSX 讀取；否則用內建讀取器直接讀取值與數字格式"
  },
  "转换为 XLSX 后作为模板，输出为 XLSX": {
    "en": "Converts to an XLSX template and exports XLSX",
    "zh-Hant": "轉換為 XLSX 後作為模板，輸出為 XLSX"
  },
  "同 XLSX": {
    "en": "Same as XLSX",
    "zh-Hant": "同 XLSX"
  },
  "输出 XLSX（不回写 XLS）": {
    "en": "Exports XLSX (does not write XLS)",
    "zh-Hant": "輸出 XLSX（不回寫 XLS）"
  },
  "经排版引擎导出": {
    "en": "Exported by the layout engine",
    "zh-Hant": "經排版引擎匯出"
  },
  "经格式转换为 XLSX 读取": {
    "en": "Converted to XLSX for reading",
    "zh-Hant": "經格式轉換為 XLSX 讀取"
  },
  "转换为 XLSX 模板": {
    "en": "Converts to an XLSX template",
    "zh-Hant": "轉換為 XLSX 模板"
  },
  "输出 XLSX": {
    "en": "Exports XLSX",
    "zh-Hant": "輸出 XLSX"
  },
  "按段落/表格结构读取；取值使用结构路径 + 前后文锚点；表格单元格使用标签锚点": {
    "en": "Reads paragraph/table structure; locates values using structural paths and context anchors, with label anchors for table cells",
    "zh-Hant": "按段落/表格結構讀取；取值使用結構路徑 + 前後文錨點；表格單元格使用標籤錨點"
  },
  "正文、表格（含合并）、图片、编号、页眉页脚；文字可划选": {
    "en": "Body, tables including merged cells, images, numbering, headers and footers; text selection",
    "zh-Hant": "正文、表格（含合併）、圖片、編號、頁眉頁腳；文字可划選"
  },
  "划选文字（可跨多个 run）替换或插入、表格单元格、表格区域、重复明细行、重复内容块（划选连续段落，「标签：值」自动成为明细列）；字段为内容控件": {
    "en": "Select text across runs for replacement or insertion, table cells/regions, repeating rows and consecutive paragraph blocks. Label:value pairs become detail columns; content controls mark fields.",
    "zh-Hant": "划選文字（可跨多個 run）替換或插入、表格單元格、表格區域、重復明細行、重復內容塊（划選連續段落，「標籤：值」自動成為明細列）；字段為內容控件"
  },
  "单值（沿用字段起始文字样式）、固定区域、重复表格行（表头跨页重复、行不跨页拆分、0/1/多行）、重复内容块（每条明细复制一组段落，保留格式/编号/图片，可空行或分页分隔）": {
    "en": "Single values retain the starting style; fixed regions; repeating table rows with repeated page headers and unsplit rows; repeating paragraph blocks preserve formatting, numbering and images, with blank-line or page-break separators",
    "zh-Hant": "單值（沿用字段起始文字樣式）、固定區域、重復表格行（表頭跨頁重復、行不跨頁拆分、0/1/多行）、重復內容塊（每條明細複製一組段落，保留格式/編號/圖片，可空行或分頁分隔）"
  },
  "未修改部件原样保留；只改写包含字段的段落": {
    "en": "Untouched parts are retained; only paragraphs containing fields are rewritten",
    "zh-Hant": "未修改部件原樣保留；只改寫包含字段的段落"
  },
  "经格式转换为 DOCX 读取": {
    "en": "Converted to DOCX for reading",
    "zh-Hant": "經格式轉換為 DOCX 讀取"
  },
  "同 DOCX": {
    "en": "Same as DOCX",
    "zh-Hant": "同 DOCX"
  },
  "转换为 DOCX 模板，输出 DOCX": {
    "en": "Converts to a DOCX template and exports DOCX",
    "zh-Hant": "轉換為 DOCX 模板，輸出 DOCX"
  },
  "输出 DOCX": {
    "en": "Exports DOCX",
    "zh-Hant": "輸出 DOCX"
  },
  "按页面坐标框选，读取框内文字；定位标签随版式移动校验，页面尺寸/旋转不同则报错": {
    "en": "Select regions by page coordinates and read text inside. Anchors verify layout shifts; page size or rotation mismatches raise errors.",
    "zh-Hant": "按頁面坐標框選，讀取框內文字；定位標籤隨版式移動校驗，頁面尺寸/旋轉不同則報錯"
  },
  "页面图像 + 文字层框选，缩放/旋转后坐标正确换算": {
    "en": "Page image plus selectable text layer; coordinates adjust correctly with zoom and rotation",
    "zh-Hant": "頁面圖像 + 文字層框選，縮放/旋轉後坐標正確換算"
  },
  "框选区域设为单值字段或重复明细区域：真正删除区域内原文字（可再次提取验证），保留表格线；明细区域按表格线/表头/样例行自动识别列与行高，可拖动调整": {
    "en": "Select single-value or repeating-detail regions. Original text is truly removed while table lines remain. Columns and row heights are detected from lines, headers and sample rows, with drag adjustment.",
    "zh-Hant": "框選區域設為單值字段或重復明細區域：真正刪除區域內原文字（可再次提取驗證），保留表格線；明細區域按表格線/表頭/樣例行自動識別列與行高，可拖動調整"
  },
  "单值文本框：自动换行、从设定字号缩小到最小字号，仍放不下则报错不截断；重复明细逐行写入，超出区域时按设置报错、复制本页续写或压缩行高": {
    "en": "Single-value boxes wrap and shrink down to the minimum size, then fail instead of truncating. Details fill row by row; overflow can fail, duplicate the page or compress row height.",
    "zh-Hant": "單值文本框：自動換行、從設定字號縮小到最小字號，仍放不下則報錯不截斷；重復明細逐行寫入，超出區域時按設置報錯、複製本頁續寫或壓縮行高"
  },
  "原生": {
    "en": "Native",
    "zh-Hant": "原生"
  },
  "按文本型读取页面文字": {
    "en": "Reads page text as a text PDF",
    "zh-Hant": "按文本型讀取頁面文字"
  },
  "显示表单字段": {
    "en": "Shows form fields",
    "zh-Hant": "顯示表單字段"
  },
  "已定义的表单字段可直接设为待填字段": {
    "en": "Existing form fields can become fillable fields directly",
    "zh-Hant": "已定義的表單字段可直接設為待填字段"
  },
  "填写后扁平化为页面文字（嵌入中文字体子集）": {
    "en": "Filled fields flatten to page text with an embedded Chinese font subset",
    "zh-Hant": "填寫後扁平化為頁面文字（嵌入中文字體子集）"
  },
  "有文字层优先直接提取；无文字层时本地文字识别（中文+英文），识别词含置信度，低置信度需人工确认，可逐项修正": {
    "en": "Uses existing text layers first; otherwise runs local text recognition (Chinese + English). Recognized words include confidence; low-confidence values require manual confirmation and can be corrected individually.",
    "zh-Hant": "有文字層優先直接提取；無文字層時本機文字辨識（中文+英文），識別詞含置信度，低置信度需人工確認，可逐項修正"
  },
  "原始图像预览与识别文字分开保存": {
    "en": "Original image previews and recognized text are stored separately",
    "zh-Hant": "原始圖像預覽與識別文字分開保存"
  },
  "扫描 PDF 与 PNG/JPEG 图片都可作为模板：框选区域按周围背景重建像素后写入（跟随渐变与光照），文字颜色与字号取自原内容；像素绘制的表格可设为重复明细区域（表格线从图像识别、列名本地 OCR 建议）": {
    "en": "Scanned PDF and PNG/JPEG templates rebuild selected pixels from surrounding gradients and lighting before writing text in the original color and size. Raster tables support repeating detail regions, with detected lines and local OCR column suggestions.",
    "zh-Hant": "掃描 PDF 與 PNG/JPEG 圖片都可作為模板：框選區域按周圍背景重建像素後寫入（跟隨漸變與光照），文字顏色與字號取自原內容；像素繪制的表格可設為重復明細區域（表格線從圖像識別、列名本地 OCR 建議）"
  },
  "单值文本框与重复明细（逐格写入，保留表格线）": {
    "en": "Single-value boxes and repeating details filled cell by cell, retaining table lines",
    "zh-Hant": "單值文本框與重復明細（逐格寫入，保留表格線）"
  },
  "图片模板输出同尺寸 PNG/JPEG（保留 DPI 与 ICC 颜色配置），也可导出 PDF；扫描 PDF 输出 PDF": {
    "en": "Image templates export same-size PNG/JPEG with DPI and ICC profiles, or PDF. Scanned PDF templates export PDF.",
    "zh-Hant": "圖片模板輸出同尺寸 PNG/JPEG（保留 DPI 與 ICC 顏色配置），也可導出 PDF；掃描 PDF 輸出 PDF"
  },
  "按幻灯片→形状→段落→字符偏移定位，表格单元格定位；形状编号变化时按唯一形状名称回退": {
    "en": "Locates by slide, shape, paragraph and character offset or table cell. Falls back to a unique shape name if shape IDs change.",
    "zh-Hant": "按幻燈片→形狀→段落→字符偏移定位，表格單元格定位；形狀編號變化時按唯一形狀名稱回退"
  },
  "按幻灯片展示文本框、表格、图片": {
    "en": "Displays text boxes, tables and images by slide",
    "zh-Hant": "按幻燈片展示文本框、表格、圖片"
  },
  "文本框内划选文字、表格单元格、表格区域、重复表格行": {
    "en": "Selected text within text boxes, table cells, regions and repeating rows",
    "zh-Hant": "文本框內划選文字、表格單元格、表格區域、重復表格行"
  },
  "单值、区域、重复表格行（超出幻灯片底部会警告，不自动分页）": {
    "en": "Single values, regions and repeating rows; warns about slide overflow without automatic pagination",
    "zh-Hant": "單值、區域、重復表格行（超出幻燈片底部會警告，不自動分頁）"
  },
  "未修改部件原样保留": {
    "en": "Untouched parts are retained",
    "zh-Hant": "未修改部件原樣保留"
  },
  "经格式转换为 PPTX": {
    "en": "Converted to PPTX",
    "zh-Hant": "經格式轉換為 PPTX"
  },
  "同 PPTX": {
    "en": "Same as PPTX",
    "zh-Hant": "同 PPTX"
  },
  "转换为 PPTX 模板": {
    "en": "Converts to a PPTX template",
    "zh-Hant": "轉換為 PPTX 模板"
  },
  "输出 PPTX": {
    "en": "Exports PPTX",
    "zh-Hant": "輸出 PPTX"
  },
  "按行 + 前后文锚点取值": {
    "en": "Locates values by line and surrounding context",
    "zh-Hant": "按行 + 前後文錨點取值"
  },
  "逐行显示，可划选": {
    "en": "Selectable line-by-line display",
    "zh-Hant": "逐行顯示，可划選"
  },
  "纯文本不作为模板（无排版能力）": {
    "en": "Plain text cannot serve as a template because it has no layout support",
    "zh-Hant": "純文本不作為模板（無排版能力）"
  },
  "转为纯文本读取（不执行脚本、不加载外部资源）；<table> 识别为表格": {
    "en": "Reads as plain text without scripts or external resources; recognizes <table> elements",
    "zh-Hant": "轉為純文本讀取（不執行腳本、不加載外部資源）；<table> 識別為表格"
  },
  "文本/表格视图（不渲染原始 HTML）": {
    "en": "Text/table view; original HTML is not rendered",
    "zh-Hant": "文本/表格視圖（不渲染原始 HTML）"
  },
  "不作为模板": {
    "en": "Not supported as a template",
    "zh-Hant": "不作為模板"
  },
  "PDF 模板、图片模板、PDF 读取（pymupdf 引擎）": {
    "en": "PDF templates, image templates and PDF reading (pymupdf engine)",
    "zh-Hant": "PDF 模板、圖片模板、PDF 讀取（pymupdf 引擎）"
  },
  "Apache-2.0 或 BSD-3-Clause（绑定）；BSD-3-Clause / Apache-2.0（PDFium）": {
    "en": "Apache-2.0 or BSD-3-Clause (bindings); BSD-3-Clause / Apache-2.0 (PDFium)",
    "zh-Hant": "Apache-2.0 或 BSD-3-Clause（綁定）；BSD-3-Clause / Apache-2.0（PDFium）"
  },
  "PDF 读取（pdfium 引擎）": {
    "en": "PDF reading (pdfium engine)",
    "zh-Hant": "PDF 讀取（pdfium 引擎）"
  },
  "LibreOffice（外部程序）": {
    "en": "LibreOffice (external program)",
    "zh-Hant": "LibreOffice（外部程序）"
  },
  "旧格式转换、PDF 导出、真实分页、公式后备计算": {
    "en": "Legacy conversion, PDF export, actual pagination and formula fallback",
    "zh-Hant": "舊格式轉換、PDF 導出、真實分頁、公式後備計算"
  },
  "Tesseract OCR（外部程序）+ chi_sim / eng 模型": {
    "en": "Tesseract OCR (external program) + chi_sim / eng models",
    "zh-Hant": "Tesseract OCR（外部程序）+ chi_sim / eng 模型"
  },
  "扫描件 / 图片本地 OCR": {
    "en": "Local OCR for scans and images",
    "zh-Hant": "掃描件 / 圖片本地 OCR"
  },
  "Web 服务": {
    "en": "Web service",
    "zh-Hant": "Web 服務"
  },
  "XLSX 读取与预览": {
    "en": "XLSX reading and preview",
    "zh-Hant": "XLSX 讀取與預覽"
  },
  "DOCX / PPTX 读取与内置模板生成": {
    "en": "DOCX / PPTX reading and built-in template generation",
    "zh-Hant": "DOCX / PPTX 讀取與內置模板生成"
  },
  "OOXML 直接编辑": {
    "en": "Direct OOXML editing",
    "zh-Hant": "OOXML 直接編輯"
  },
  "图片处理": {
    "en": "Image processing",
    "zh-Hant": "圖片處理"
  },
  "字体子集": {
    "en": "Font subsetting",
    "zh-Hant": "字體子集"
  },
  "argon2-cffi（含 argon2-cffi-bindings、cffi、pycparser）": {
    "en": "argon2-cffi (including argon2-cffi-bindings, cffi and pycparser)",
    "zh-Hant": "argon2-cffi（含 argon2-cffi-bindings、cffi、pycparser）"
  },
  "口令哈希（Argon2id；参数经部署机实测，见 synwevo_core/passwords.py）": {
    "en": "Password hashing (Argon2id; parameters measured on the deployment host; see synwevo_core/passwords.py)",
    "zh-Hant": "口令哈希（Argon2id；參數經部署機實測，見 synwevo_core/passwords.py）"
  },
  "TOTP 第二因素": {
    "en": "TOTP second factor",
    "zh-Hant": "TOTP 第二因素"
  },
  "Apache-2.0 或 BSD-3-Clause": {
    "en": "Apache-2.0 or BSD-3-Clause",
    "zh-Hant": "Apache-2.0 或 BSD-3-Clause"
  },
  "TOTP 种子落地加密（AES-256-GCM）、WebAuthn 签名校验": {
    "en": "Encrypted TOTP seed storage (AES-256-GCM) and WebAuthn signature verification",
    "zh-Hant": "TOTP 種子落地加密（AES-256-GCM）、WebAuthn 簽名校驗"
  },
  "Passkey / WebAuthn 注册与登录仪式（协议实现）": {
    "en": "Passkey / WebAuthn registration and login protocol",
    "zh-Hant": "Passkey / WebAuthn 註冊與登錄儀式（協議實現）"
  },
  "WebAuthn 的 COSE 与证书解析依赖": {
    "en": "WebAuthn COSE and certificate parsing dependencies",
    "zh-Hant": "WebAuthn 的 COSE 與證書解析依賴"
  },
  "OAuth 2.0 / OpenID Connect 授权服务器与客户端（协议实现）": {
    "en": "OAuth 2.0 / OpenID Connect authorization server and client protocol",
    "zh-Hant": "OAuth 2.0 / OpenID Connect 授權服務器與客戶端（協議實現）"
  },
  "CSV 编码识别": {
    "en": "CSV encoding detection",
    "zh-Hant": "CSV 編碼識別"
  },
  "XLS 读取、旧格式检测": {
    "en": "XLS reading and legacy format detection",
    "zh-Hant": "XLS 讀取、舊格式檢測"
  },
  "界面": {
    "en": "User interface",
    "zh-Hant": "界面"
  },
  "TipTap（开源部分）": {
    "en": "TipTap (open source components)",
    "zh-Hant": "TipTap（開源部分）"
  },
  "内置文档编辑器": {
    "en": "Built-in document editor",
    "zh-Hant": "內置文檔編輯器"
  },
  "Droid Sans Fallback（随 PyMuPDF 分发的字体）": {
    "en": "Droid Sans Fallback (font distributed with PyMuPDF)",
    "zh-Hant": "Droid Sans Fallback（隨 PyMuPDF 分發的字體）"
  },
  "PDF 中文字体": {
    "en": "Chinese font for PDFs",
    "zh-Hant": "PDF 中文字體"
  },
  "Synwevo Docs · 文档工作台": {
    "en": "Synwevo Docs · Document workspace",
    "zh-Hant": "Synwevo Docs · 文檔工作台"
  },
  "操作失败，请检查设置后重试。": {
    "en": "The operation failed. Check the settings and try again.",
    "zh-Hant": "操作失敗，請檢查設置後重試。"
  },
  "请求未完成（{code}）。": {
    "en": "The request could not be completed ({code}).",
    "zh-Hant": "請求未完成（{code}）。"
  },
  "请求编号：{id}": {
    "en": "Request ID: {id}",
    "zh-Hant": "請求編號：{id}"
  },
  "网络连接失败，请检查连接后重试。": {
    "en": "Network connection failed. Check your connection and try again.",
    "zh-Hant": "網絡連接失敗，請檢查連接後重試。"
  },
  "服务内部出错，请稍后重试。": {
    "en": "The service encountered an error. Try again later.",
    "zh-Hant": "服務內部出錯，請稍後重試。"
  },
  "请求被前置网关拦截，未到达 Synwevo。": {
    "en": "A gateway blocked the request before it reached Synwevo.",
    "zh-Hant": "請求被前置網關攔截，未到達 Synwevo。"
  },
  "前置网关无法连接 Synwevo，请稍后重试。": {
    "en": "The gateway could not reach Synwevo. Try again later.",
    "zh-Hant": "前置網關無法連接 Synwevo，請稍後重試。"
  },
  "请求过于频繁，请稍后重试。": {
    "en": "Too many requests. Try again later.",
    "zh-Hant": "請求過於頻繁，請稍後重試。"
  },
  "请重新登录后再试。": {
    "en": "Sign in again and retry.",
    "zh-Hant": "請重新登錄後再試。"
  },
  "你没有执行此操作的权限。": {
    "en": "You do not have permission for this action.",
    "zh-Hant": "你沒有執行此操作的權限。"
  },
  "找不到请求的资源。": {
    "en": "The requested resource was not found.",
    "zh-Hant": "找不到請求的資源。"
  },
  "请求参数无效，请检查设置。": {
    "en": "Invalid request parameters. Check the settings.",
    "zh-Hant": "請求參數無效，請檢查設置。"
  },
  "文件或请求超过大小上限。": {
    "en": "The file or request exceeds the size limit.",
    "zh-Hant": "文件或請求超過大小上限。"
  },
  "该格式不支持此操作。": {
    "en": "This format does not support this action.",
    "zh-Hant": "該格式不支持此操作。"
  },
  "请先选择目标模板。": {
    "en": "Choose a target template first.",
    "zh-Hant": "請先選擇目標模板。"
  },
  "模板仍被项目使用，无法删除。": {
    "en": "This template is used by projects and cannot be deleted.",
    "zh-Hant": "模板仍被項目使用，無法刪除。"
  },
  "模板文件已加密，请移除密码后再上传。": {
    "en": "The template is encrypted. Remove its password before uploading.",
    "zh-Hant": "模板文件已加密，請移除密碼後再上傳。"
  },
  "安全检查未通过：文件被判定为恶意内容，已拒绝。": {
    "en": "Security check failed: the file was identified as malicious content and has been rejected.",
    "zh-Hant": "安全檢查未通過：文件被判定為惡意內容，已拒絕。"
  },
  "安全检查服务暂不可用，请稍后重试。": {
    "en": "The security check service is temporarily unavailable. Please try again later.",
    "zh-Hant": "安全檢查服務暫不可用，請稍後重試。"
  },
  "文件超过安全检查的大小上限，无法接纳；请拆分或压缩后重新上传。": {
    "en": "The file exceeds the security check size limit and cannot be accepted. Split or compress it and upload again.",
    "zh-Hant": "文件超過安全檢查的大小上限，無法接納；請拆分或壓縮後重新上傳。"
  },
  "该文件尚未通过安全检查，暂不能下载。": {
    "en": "This file has not passed the security check yet and cannot be downloaded.",
    "zh-Hant": "該文件尚未通過安全檢查，暫不能下載。"
  },
  "该文件未通过安全检查，已被拒绝并隔离。": {
    "en": "This file failed the security check and has been rejected and quarantined.",
    "zh-Hant": "該文件未通過安全檢查，已被拒絕並隔離。"
  },
  "当前没有可以撤销的修改。": {
    "en": "There are no changes to undo.",
    "zh-Hant": "當前沒有可以撤銷的修改。"
  },
  "当前没有可以重做的修改。": {
    "en": "There are no changes to redo.",
    "zh-Hant": "當前沒有可以重做的修改。"
  },
  "生成队列已满，请等待当前任务完成。": {
    "en": "The generation queue is full. Wait for active jobs to finish.",
    "zh-Hant": "生成隊列已滿，請等待當前任務完成。"
  },
  "来源文件尚未就绪，请等待解析完成。": {
    "en": "Source files are not ready. Wait for parsing to finish.",
    "zh-Hant": "來源文件尚未就緒，請等待解析完成。"
  },
  "没有识别到样本，请检查样本规则。": {
    "en": "No samples were detected. Check the sample rules.",
    "zh-Hant": "沒有識別到樣本，請檢查樣本規則。"
  },
  "请先修正样本中的错误。": {
    "en": "Fix the sample errors first.",
    "zh-Hant": "請先修正樣本中的錯誤。"
  },
  "输出文件已被删除或清理，请重新生成。": {
    "en": "The output file was deleted or cleaned up. Generate it again.",
    "zh-Hant": "輸出文件已被刪除或清理，請重新生成。"
  },
  "请先设置样本规则。": {
    "en": "Set sample rules first.",
    "zh-Hant": "請先設置樣本規則。"
  },
  "服务器正在进行其他模拟生成，请稍后重试。": {
    "en": "Another simulation is running. Try again later.",
    "zh-Hant": "服務器正在進行其他模擬生成，請稍後重試。"
  },
  "该请求标识已用于不同内容，请重新发起操作。": {
    "en": "This request identifier was used for different content. Start the operation again.",
    "zh-Hant": "該請求標識已用於不同內容，請重新發起操作。"
  },
  "任务仍在进行中，请等待结束或先取消。": {
    "en": "The job is still active. Wait for it to finish or cancel it first.",
    "zh-Hant": "任務仍在進行中，請等待結束或先取消。"
  },
  "没有可重试的样本。": {
    "en": "There are no samples to retry.",
    "zh-Hant": "沒有可重試的樣本。"
  },
  "所需的本地处理组件不可用，请联系管理员。": {
    "en": "A required local processing component is unavailable. Contact your administrator.",
    "zh-Hant": "所需的本地處理組件不可用，請聯繫管理員。"
  },
  "文件转换失败，请检查文件与本地转换组件。": {
    "en": "File conversion failed. Check the file and local conversion component.",
    "zh-Hant": "文件轉換失敗，請檢查文件與本地轉換組件。"
  },
  "字段内容无法完整放入目标区域。": {
    "en": "The field content does not fit completely in the target region.",
    "zh-Hant": "字段內容無法完整放入目標區域。"
  },
  "内容超过允许的模板页数。": {
    "en": "The output exceeds the allowed template page count.",
    "zh-Hant": "內容超過允許的模板頁數。"
  },
  "为保持页数，已缩小允许缩小的字段。": {
    "en": "Eligible fields were shrunk to preserve the page count.",
    "zh-Hant": "為保持頁數，已縮小允許縮小的字段。"
  },
  "无法完成真实分页检查。": {
    "en": "Actual pagination could not be checked.",
    "zh-Hant": "無法完成真實分頁檢查。"
  },
  "输出内容验证未通过。": {
    "en": "Output content verification failed.",
    "zh-Hant": "輸出內容驗證未通過。"
  },
  "第 {count} 张幻灯片": {
    "en": "Slide {count}",
    "zh-Hant": "第 {count} 張幻燈片"
  },
  "第 {count} 块，共 {total} 块": {
    "en": "Block {count} of {total}",
    "zh-Hant": "第 {count} 塊，共 {total} 塊"
  },
  "第 {lines} 行还不是「原值=新值」，暂不生效": {
    "en": "Lines {lines} do not yet use old=new and are not applied",
    "zh-Hant": "第 {lines} 行還不是「原值=新值」，暫不生效"
  },
  "{count} 个项目": {
    "en": {
      "one": "{count} project",
      "other": "{count} projects"
    },
    "zh-Hant": "{count} 個項目"
  },
  "{format}（模板原生格式）": {
    "en": "{format} (native template format)",
    "zh-Hant": "{format}（模板原生格式）"
  },
  "{format}（与模板图片同尺寸）": {
    "en": "{format} (same size as template image)",
    "zh-Hant": "{format}（與模板圖片同尺寸）"
  },
  "PDF（经排版引擎转换）": {
    "en": "PDF (converted by the layout engine)",
    "zh-Hant": "PDF（經排版引擎轉換）"
  },
  "此部署未启用排版引擎，无法导出 PDF": {
    "en": "PDF export is unavailable: the layout engine is not enabled on this server",
    "zh-Hant": "此部署未啟用排版引擎，無法匯出 PDF"
  },
  "CSV（仅第一个工作表的值，不含样式）": {
    "en": "CSV (first worksheet values only, without styles)",
    "zh-Hant": "CSV（僅第一個工作表的值，不含樣式）"
  },
  "CSV 只保存单元格值，不保存字体、颜色、合并单元格和多工作表；公式写出计算结果": {
    "en": "CSV preserves cell values only, without fonts, colors, merges or multiple sheets; formulas export their calculated values",
    "zh-Hant": "CSV 只保存單元格值，不保存字體、顏色、合併單元格和多工作表；公式寫出計算結果"
  },
  "文档型模板无法直接导出为 Excel。替代方案：改用表格型模板（内置表格模板或上传 XLSX 模板）生成 Excel，或导出 DOCX / PDF": {
    "en": "Document templates cannot export directly to Excel. Use a built-in or uploaded spreadsheet template, or export DOCX / PDF instead.",
    "zh-Hant": "文檔型模板無法直接導出為 Excel。替代方案：改用表格型模板（內置表格模板或上傳 XLSX 模板）生成 Excel，或導出 DOCX / PDF"
  },
  "文档型模板没有行列结构，无法导出 CSV。替代方案：使用表格型模板，并选择 CSV 导出": {
    "en": "Document templates have no row/column structure for CSV. Use a spreadsheet template and choose CSV export.",
    "zh-Hant": "文檔型模板沒有行列結構，無法導出 CSV。替代方案：使用表格型模板，並選擇 CSV 導出"
  },
  "PDF（单页，嵌入生成的图片）": {
    "en": "PDF (one page with the generated image)",
    "zh-Hant": "PDF（單頁，嵌入生成的圖片）"
  },
  "PDF 模板没有可编辑的文档结构，不能无损转换为 Word。请上传 DOCX 模板或用内置文档模板重建": {
    "en": "PDF templates have no editable document structure for lossless Word conversion. Upload a DOCX template or rebuild it in the document editor.",
    "zh-Hant": "PDF 模板沒有可編輯的文檔結構，不能無損轉換為 Word。請上傳 DOCX 模板或用內置文檔模板重建"
  },
  "页眉（第 {section} 节）": {
    "en": "Header (section {section})",
    "zh-Hant": "頁眉（第 {section} 節）"
  },
  "页脚（第 {section} 节）": {
    "en": "Footer (section {section})",
    "zh-Hant": "頁腳（第 {section} 節）"
  },
  "当前部署的 PDF 引擎处于基础模式，PDF 模板不可用；PDF 来源文件的读取、预览、OCR 与取值不受影响。如需 PDF 模板，请管理员按部署文档启用 PDF 引擎完整模式": {
    "en": "The PDF engine on this server runs in basic mode, so PDF templates are unavailable. Source PDF reading, preview, OCR and value extraction remain available. Ask an administrator to enable the PDF engine's full mode (see the deployment guide).",
    "zh-Hant": "當前部署的 PDF 引擎處於基礎模式，PDF 模板不可用；PDF 來源文件的讀取、預覽、OCR 與取值不受影響。如需 PDF 模板，請管理員按部署文件啟用 PDF 引擎完整模式"
  },
  "图片模板需要一个可绘制中文的字体文件，但本机没有找到。请安装中文字体（Debian/Ubuntu：apt install fonts-noto-cjk），或用 SYNWEVO_DOCS_CJK_FONT_FILE 指定字体文件路径": {
    "en": "Image templates require a Chinese font. Install a CJK font (Debian/Ubuntu: apt install fonts-noto-cjk) or configure SYNWEVO_DOCS_CJK_FONT_FILE.",
    "zh-Hant": "圖片模板需要一個可繪制中文的字體文件，但本機沒有找到。請安裝中文字體（Debian/Ubuntu：apt install fonts-noto-cjk），或用 SYNWEVO_DOCS_CJK_FONT_FILE 指定字體文件路徑"
  }
} satisfies Messages;
