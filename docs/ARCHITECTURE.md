# 架构说明

## 1. 总体结构

```
浏览器（React）                       服务端（Python，单进程）
┌──────────────────────┐   HTTP   ┌──────────────────────────────────────────────┐
│ 项目列表 / 模板库      │ ───────▶ │ api/app.py      REST 接口、上传下载、静态界面   │
│ 工作台：左来源｜右模板  │          │ services.py     项目/来源/模板/映射的业务操作    │
│ 映射列表、样本、校验    │          │ locators.py     选区 → 定位规则；定位 → 屏幕位置 │
│ 预览、生成、任务页      │          │ views.py        各格式查看、预览（与生成同路径） │
│ 内置文档 / 表格编辑器   │          │ jobs.py         任务快照、后台执行、校验、ZIP    │
└──────────────────────┘          │ store.py        SQLite + 内容寻址文件存储         │
                                  │ engine/         ★ 与 Web 无关的处理引擎            │
                                  └──────────────────────────────────────────────┘
                                           │ 子进程（超时、CPU 限制、私有配置）
                                           ▼
                                  LibreOffice（转换/PDF 导出/公式计算）  Tesseract（OCR）
```

`apps/docweave/docweave/engine/` 不依赖 FastAPI，可以被将来的桌面端直接调用。

## 2. 引擎模块

| 模块 | 职责 |
|---|---|
| `detect.py` | 按文件内容（而非扩展名）识别格式、加密、宏 |
| `sources.py` | 解析来源文件：表格类 → `Workbook`；文档类保留原生包；PDF/图片元数据；文本/HTML |
| `tabular.py` | CSV（编码/分隔符/RFC 4180）、XLSX（openpyxl 只读）、XLS（xlrd）读取；表头与列绑定（按名称，重名需消歧） |
| `values.py` / `numfmt.py` | 带类型的值（文本/整数/小数/日期/布尔/空），显示文本与规范值分离；Excel 数字格式渲染 |
| `resolve.py` | 按定位规则在**任意**来源文件上取值：列、固定单元格（标签校验）、区域、明细表、DOCX 文字（结构路径 + 前后文锚点）、DOCX 单元格、PDF 区域（版式校验 + 标签平移）、OCR 区域（置信度）、文本行、PPTX |
| `records.py` | 样本划分：每行 / 每文件 / 每组（组内排序、跨文件开关、空值与重复编号报告） |
| `transforms.py` | 受控转换（去空格、日期格式、小数位、前后缀、默认值、对照替换、补位……）、组内聚合、多来源组合 |
| `plan.py` | **执行计划**：一个样本 + 映射 + 字段 → 每个字段的值与问题清单。预览与批量生成都调用 `build_plan` |
| `docx_core.py` / `docx_render.py` | DOCX 段落文字模型（跨 run 偏移）、run 拆分、内容控件字段、填充；结构化渲染 |
| `xlsx_doc.py` / `refshift.py` | XLSX 直接 XML 改写：写单元格、插入/删除行时平移所有引用 |
| `pdf_engine.py` | PDF 坐标换算、区域取值、标签定位、redaction、写入与缩放 |
| `pptx_engine.py` | PPTX 结构、渲染、字段、填充 |
| `templates.py` | 各格式「设为待填区域」与填充入口 |
| `builtin.py` | 内置编辑器 JSON → DOCX / XLSX（带字段） |
| `generate.py` | 原生填充与导出格式（含可用性判断与原因） |
| `converters.py` / `ocr.py` | LibreOffice 与 Tesseract 子进程封装 |

## 3. 数据模型（SQLite，`apps/docweave/docweave/store.py`）

| 表 | 关键字段 | 说明 |
|---|---|---|
| `projects` | `template_id`、`record_rule`(JSON)、`output`(JSON)、`mapping_rev`、`rev` | 任何影响结果的修改都会递增 `rev`，旧预览随之失效 |
| `source_files`（SourceFile） | `blob`(原文件哈希)、`working_blob`(转换后)、`parsed_blob`、`format/family/status/error/notes/structure/signature`、`version`、`representative`、`ocr`、`corrections` | 原文件只读保存；修改解析选项、OCR、校正都会递增 `version` |
| `templates` + `template_versions`（TemplateVersion） | `original_blob`（原始模板，永不修改）、`head`；版本：`working_blob`、`slots`(JSON)、`editor`、`parent_no` | 每次字段增删改生成新版本；撤销/重做只移动 `head`；可复制模板 |
| 字段 TargetSlot | 存在版本的 `slots` JSON 中：`id`（稳定）、`name`、`kind`、`binding`、`locator`、`data_type`、`required`、`format`、`default`… | 同名多处字段各有独立 ID，可共享 `binding` |
| RecordRule | `projects.record_rule` | `mode`、`table`（工作表/表头/范围/过滤）、`group`（分组键/排序/跨文件） |
| `mapping_revs`（MappingRule 集合） | `rev`、`parent_rev`、`data`(映射数组) | 每次保存一个修订；撤销/重做移动项目的 `mapping_rev` |
| `jobs`（GenerationJob） | `snapshot`（来源版本、模板版本、字段、映射、规则）、`options`、`counts`、`request_id`(唯一) | 任务固定使用创建时的快照；`request_id` 防止重复提交 |
| `job_items` | `record`、`status`、`issues`、`attempts`、`file_base` | 每个样本一条；状态 pending/running/success/failed/skipped/cancelled |
| `artifacts`（OutputArtifact） | `blob`、`filename`、`format`、`trace`（来源文件/样本/字段来源）、`verification` | 每个输出可追溯到来源文件与样本 |

文件内容以 SHA-256 命名保存在 `data/blobs/`，写入采用临时文件 + 原子替换；同名输出靠去重命名（`(2)`）而不是覆盖。

## 4. 定位规则（不保存屏幕坐标 / DOM 索引）

| 来源 | 保存内容 | 在其他文件上如何校验 |
|---|---|---|
| 表格列 | 工作表名、表头行、列名（重名时第几个） | 列按名称查找，列顺序变化不影响；缺列/重名/工作表缺失或歧义 → 具体错误 |
| 固定单元格 | 工作表、单元格、旁边的标签文字与相对位置 | 标签不一致时查找唯一的同名标签并跟随；缺失或多处 → 错误 |
| DOCX 文字 | 部件、结构路径、前后文（各 24 字） | 先在同一路径按前后文截取；否则全文唯一匹配；缺失/多处 → 错误 |
| DOCX 表格单元格 / 表格 | 表格路径、行列、左侧或上方标签 / 表头文字 | 标签或表头签名匹配 |
| PDF 区域 | 页码、未旋转坐标、页面尺寸、旋转、附近标签及其位置 | 尺寸/旋转不同 → 错误；标签移动时区域随之平移；标签缺失/歧义 → 错误 |
| OCR 区域 | 页码、归一化区域、置信度阈值 | 低置信度 → 待确认；人工修正值随文件保存 |
| PPTX | 幻灯片、形状 ID、形状名称、段落、偏移、前后文 | 形状 ID 不一致时用唯一名称回退 |

模板字段在工作副本中以结构化方式存在：DOCX 内容控件、XLSX 定义名称、PDF 删除原内容的工作副本 + 字段矩形、PPTX 拆分后的 run + 字段定位。

## 5. 预览与生成使用同一路径

```
ProjectContext → records.build_records → plan.build_plan(record)
      → generate.fill_native(template, working copy, slots, plan)   ← 预览与任务共用
      → (任务) convert_output → verify_output（重新打开并逐值核对）→ 保存 artifact
```

预览返回真实生成的文件（可下载）及其渲染；预览结果按「项目修订 + 样本」缓存，任何修改都会使其失效。

## 6. 后台任务

* 创建任务时：计算全部样本的执行计划，按错误策略（「有错误则不开始」/「跳过错误样本」）决定；预先生成去重后的文件名；保存完整快照。
* 执行：单独线程逐项处理；每项先生成原生格式，再按需转换（PDF/CSV），每个文件都重新打开并核对已填写的值，核对失败 → 该项失败并记录原因。
* 取消：设置标记，当前项完成后停止，其余项标为「已取消」。
* 重试：把失败/已取消项重新排队（使用同一快照），`attempts` 递增，已成功项的文件不变。
* 恢复：服务启动时把「进行中」的任务与项目恢复为待执行，继续完成。
* 下载：单个文件、ZIP（含 `生成清单.csv`）、结果清单与失败说明 CSV。

## 7. 安全措施

* 上传大小、ZIP 展开大小与条目数、单元格数量、PDF 页数、图片像素数量均有上限（环境变量可调）；压缩包中的绝对路径与 `..` 被拒绝。
* XML 解析禁用外部实体与网络访问；HTML 只提取文本与表格，界面不渲染原始 HTML；宏、ActiveX、PDF JavaScript 不执行。
* 文件名：显示名与输出名去除非法字符、路径分隔符、`..`、Windows 保留名，并限制字节长度；存储路径只使用哈希。
* LibreOffice / Tesseract 以子进程运行：超时、CPU 时间限制、私有配置目录（每个服务进程一个）。
* 下载使用 `Content-Disposition` + `nosniff`；接口响应 `Cache-Control: no-store`。
* 单人本地部署不包含登录；多人部署请在反向代理上启用认证并为不同团队运行独立实例 / 数据目录（见 `docs/DEPLOY.md`）。
