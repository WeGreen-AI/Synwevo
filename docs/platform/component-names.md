# 自有界面里的能力名称，而不是底层组件名（CI-28）

Synwevo 的产品界面、业务流程提示、错误文案、邮件与生成产物的自有元数据用**能力**说话：排版引擎、格式转换、
本地文字识别、PDF 引擎、安全检查。底层组件的真实名称留在它们该在的地方——依赖清单、许可证与归属页、
内部技术文档、排障记录、配置项、机器契约字段。`scripts/check_component_names.py` 把这条线机械化。

## 1. 决策记录

| 项目 | 结论 |
|---|---|
| 问题与基线 | 盘点（2026-09-26，源码 + 隔离实例三语言实跑）：Docs 能力矩阵、生成对话框、预览徽标、OCR 按钮、关于页正文、编辑器提示、模板选择器与十余条引擎错误/说明文案直接写出 LibreOffice（81 处）、PyMuPDF/pdfium（22）、Tesseract（19）、xlrd；账户外壳的网关错误把拦截方按厂商命名（Cloudflare）；任务校验徽标的提示写出 python-docx/openpyxl/PDFium/Pillow；经排版引擎转换的 PDF 产物 `Producer`/`Creator` 写着转换器的名字。用户读到的是实现细节而不是产品能力，且不同页面同一能力的叫法不一致。 |
| 成熟参考 | 平台内已有先例：`check_brand.py` 对旧品牌残留的处理方式——分类、例外表要写理由、不做全仓库盲替换；本批次沿用同一形状。第九节 9.9 的边界（依赖名、协议标识、许可证与归属声明保持真实；外部登录主体是业务身份不改名）直接来自用户要求。 |
| 对比与选择 | (a) 全仓库替换字符串：会改坏包名、导入路径、环境变量、机器契约（`pdf_engine: "pymupdf"`）与许可证声明，否决。(b) 只改几处显眼文案：不可持续，下一批功能又会把名字带回来，否决。(c) **能力词表 + 守卫 + 例外表**（本方案）：词表固定三语言叫法；守卫扫描前端消息目录、`tr()/t()` 字面量、Python 自然语言字面量（错误、说明、能力行、邮件）；许可证/归属页整文件例外，其余例外逐条写理由。保留：`licensing.py` 关于与许可页、`environment.*` 机器字段、配置键、URL、注释与文档里的真实名称。 |
| 预期变化 | 见 §2 词表与 §3 清单。用户看到「排版引擎未启用」而不是「未安装 LibreOffice」；能力矩阵的处理列说「服务端本地（PDF 引擎完整模式）」；任务校验提示说「已用表格读取器重新打开」；网关拦截提示说「前置网关」，厂商信息留在诊断 `detail.server`；转换得到的 PDF 产物 `Producer`/`Creator` 为 `Synwevo Docs`，作者/标题/关键词等模板自带字段保持不变。 |
| 边界与风险 | 不改：关于与许可页的第三方组件表与 AGPL 通知（法律义务，`ATTRIBUTION_FILES`）；`environment.pdf_engine_license` 字段（许可声明，运营读取，外壳不显示，列入 `STABLE`）；引擎 id `pymupdf`/`pdfium`、`SYNWEVO_DOCS_PDF_ENGINE`、扫描记录 `scan.engine`（机器契约）；Google/Microsoft 登录按钮（业务身份）；Turnstile 脚本 URL 与 `provider: "turnstile"` 字段（技术标识，非文案）；用户文档、文件名与其自带元数据。PDF 产物元数据只在 PDF 引擎完整模式下重写，基础模式（pypdfium2 无写入能力）原样返回。人机验证组件本身的 iframe 由供应商渲染（其可见品牌不由 Synwevo 控制，见 §4）。 |
| 验收 | `check_component_names.py` 通过（4 条列出的例外）；Docs 套件相关测试更新并通过（`test_pdfium_engine`、`test_image_raster`、`test_image_template`、`test_output_metadata`）；外壳测试通过（网关文案断言改为角色 + 诊断字段）；浏览器脚本 `ui_e2e_component_names.py` 在 Chromium + WebKit 三语言读取能力矩阵、关于页正文、首页、项目页、预览、生成对话框、任务页与全部 `title` 提示，均无组件名，关于页表格仍写明 PDF 引擎的真实归属。 |
| 发布与恢复 | 纯文案与元数据变化，无 schema 变化；接口机器字段不变（任务校验 `engine` 由库名改为格式族 id `docx/xlsx/pdf/pptx/csv/image`，外壳据此显示能力名——这是 `verification` 结果里的展示字段，不在 SDK 契约中）。回滚无数据影响。 |

## 2. 词表

| 底层组件 | 能力名称（zh-Hans / zh-Hant / en） | 用在哪里 |
|---|---|---|
| LibreOffice（排版、转换、公式后备） | 排版引擎 / 排版引擎 / the layout engine；格式转换 / 格式轉換 / conversion | 能力矩阵、生成对话框布局选项、预览徽标、公式回退说明、导出 PDF、错误文案 |
| PyMuPDF（完整）/ pypdfium2（基础） | PDF 引擎完整模式 / PDF 引擎完整模式 / the PDF engine's full mode；PDF 引擎基础模式 / PDF 引擎基礎模式 / basic mode | PDF 模板不可用、图片模板重复明细、页内切分、文字样式的提示 |
| Tesseract | 本地文字识别（OCR）/ 本機文字辨識（OCR）/ local text recognition (OCR) | OCR 按钮、能力矩阵、关于页正文、OCR 错误 |
| xlrd | 内置读取器 / 內建讀取器 / the built-in reader | XLS 无排版引擎时的读取说明 |
| python-docx / openpyxl / python-pptx / Pillow（任务校验重开） | 文档读取器 / 表格读取器 / 演示文稿读取器 / 图像读取器（+ PDF 引擎、CSV 读取器） | 任务页校验徽标提示 |
| Cloudflare / 反向代理（拦截或不可达） | 前置网关 / 前置閘道 / Gateway | 账户外壳网关错误；厂商线索留在 `detail.server` |
| ClamAV / clamd | 安全检查 / 安全檢查 / security check（已有） | 上传接纳链状态 |
| Word / Excel（用户自己的软件） | 桌面办公软件；「Excel 打开时重算」保留——那是用户的软件，不是我们的组件 | 编辑器分页提示、公式说明 |

## 3. 本批次改动的表面

* Docs 后端：`capabilities.py`（能力矩阵 24 处）、`engine/generate.py`、`engine/pdf_engine.py`（`TEMPLATES_UNAVAILABLE`、`stamp_output_metadata`）、`engine/templates.py`、`engine/image_raster.py`、`engine/blocks.py`、`engine/converters.py`、`engine/layout.py`、`engine/ocr.py`、`engine/xlsx_cache.py`、`engine/sources.py`、`engine/pdfium_shim.py`、`services.py`、`jobs.py`（校验 `engine` 改为格式族）。
* Docs 外壳：`messages.server.ts`（19 条后端文案的键与三语言）、`messages.ts`（10 条 + 9 条新能力名）、`GenerateDialog`、`PreviewPane`、`TemplateChooser`、`About`、`Capabilities`（环境行只说已启用/未启用，不再打印版本串）、`DocEditor`、`Workspace`、`lib/presentation.ts`、`lib/jobview.ts`。
* 核心/账户：`synwevo_core.malware` 配置错误文案；`auth-client/api.ts` 网关文案与 `detail.server`。
* 守卫：`scripts/check_component_names.py`（CI 与验证清单接入）。

## 4. 明确不改或做不到的

* **人机验证小部件**：Cloudflare Turnstile 的 iframe 内容由供应商渲染，其可见品牌不在 Synwevo 控制范围；Synwevo 自有包装（标题「人机验证」、加载/过期/失败提示）已是能力名。若要去掉该品牌暴露，是「是否继续使用该供应商」的独立产品决定，不在文案层解决。
* **关于与许可页**：AGPL-3.0 §13 要求写明 PyMuPDF 并提供源代码包；MPL/Apache 组件的归属表照旧。这一页是给要核实许可义务的人看的。
* **PDF 引擎基础模式下的 PDF 产物元数据**：pypdfium2 不能改写 trailer，`Producer` 保持转换器写入的值；能力矩阵与本文记录了这一点。
* **外部登录主体**：Google / Microsoft 按钮与其官方页面按 9.9 第 7 条保留。
