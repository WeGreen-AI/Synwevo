# 验证记录（实际执行的命令与结果）

2026-09-25 本轮结果以 [handoff.md](handoff.md) 为单一当前入口。下文保留历轮记录；旧环境、旧目录、测试数量和 UI 约束不得作为本轮验收证据。

本文只记录**实际运行过**的检查及其输出。未运行的检查在文末「未覆盖」一节如实列出，不计入通过项。
问题与修复证据见 [issues.md](issues.md)。

## 环境

| 项 | 值 |
|---|---|
| 机器 | Apple Silicon（arm64），15 核 |
| 系统 | macOS，Darwin 27.0.0 |
| Python | 3.14.5（`backend/.venv`） |
| 前端 | Node / Vite 8、TypeScript、React 19 |
| 浏览器 | Google Chrome 152（Playwright `channel="chrome"`；端到端用 headless，帧时间测量用**可见窗口**） |
| 显示器 | 约 120 Hz（空闲帧间隔实测中位 8.3 ms） |
| LibreOffice | 26.8.0.3 |
| Tesseract | 5.x，`chi_sim` + `eng` |
| PDF 引擎 | PyMuPDF（AGPL / 商业许可，见 `docs/DEPENDENCIES.md`） |
| 服务 | `backend/run.py`（uvicorn 单进程），`127.0.0.1:8765` |
| 数据 | 全部为合成测试文件（`testdata/`，由 `scripts/make_fixtures.py` 生成），未使用任何真实业务数据 |
| 日期 | 2026-09-23 |

## 1. 自动化测试

```bash
backend/.venv/bin/python -m pytest -q          # 在 backend/ 下运行
```

**结果：202 passed, 1 skipped**（约 95 秒）。跳过项为需要外部依赖的用例（见 `docs/TESTING.md`）。

本轮新增/修改的用例：

| 文件 | 数量 | 锁定的行为 |
|---|---|---|
| `tests/test_listen_guard.py` | 6（12 个参数化实例） | 非本机监听 + 无界面鉴权必须拒绝启动；容器内改为警告；直接运行 `backend/run.py` 断言退出码非 0 |
| `tests/test_large_sheet.py` | 1 | 大表分页复用已解析工作簿（统计重建次数） |
| `tests/test_api_v1.py::test_every_response_carries_the_trace_id` | 1 | 鉴权失败的响应也带 `X-Request-ID`，且与响应体一致、可由调用方指定 |
| `tests/test_per_block.py` | 4（修复） | 不再依赖运行目录 |
| `tests/test_slot_options.py` | 15 | 字段枚举（`empty_policy`/`data_type`/`align`/`valign`）在创建与 PATCH 两条路径上都拒绝未知值 |
| `tests/test_job_reliability.py::test_unknown_layout_is_rejected_not_silently_downgraded` | 1 | `layout` 非法值返回 400 而不是静默关闭分页检查 |
| `tests/test_engine.py::test_combine_rejects_unknown_op` | 1 | 未知合并方式抛错，不再静默拼接 |

两个新回归测试都验证过「没有修复时会失败」：恢复旧调用后 `test_large_sheet.py` 报「分页时重建了 6 次工作簿对象」；修复前 `SYNWEVO_HOST=0.0.0.0` 实际可以启动并提供服务。

## 2. 构建与类型检查

```bash
cd frontend && npx tsc --noEmit && npm run build
```

**结果：类型检查无输出（通过）；生产构建成功**（`✓ built in 98–217ms`，`index.js` 471 KB / gzip 146 KB，`index.css` 28.8 KB / gzip 6.7 KB）。

## 3. 干净环境启动与部署一致性

```bash
SYNWEVO_DATA_DIR=<空目录> SYNWEVO_PORT=8799 .venv/bin/python -m synwevo_server.run
```

| 检查 | 结果 |
|---|---|
| `/healthz` | `{"status":"ok","version":"1.0.0"}` |
| `/readyz` | `{"status":"ready","checks":{"database":"ok","storage":"ok","worker":"ok"},"schema_version":2}` |
| 界面首页 | HTTP 200 |
| `/api/v1/capabilities` 无密钥 | HTTP 401 |
| `/api/docs` 默认 | HTTP 404（未配置 `SYNWEVO_API_DOCS`） |
| 新建数据目录内容 | `blobs/ docweave.sqlite3(+wal,shm) tmp/` |

监听与鉴权守卫：

| 场景 | 结果 |
|---|---|
| `SYNWEVO_HOST=0.0.0.0`，`SYNWEVO_UI_AUTH` 未设 | **退出码 1**，打印「拒绝启动…」及三种处理方式 |
| 同上 + `SYNWEVO_UI_AUTH=proxy` | 正常启动：`Uvicorn running on http://0.0.0.0:8798` |
| 容器（`SYNWEVO_IN_CONTAINER=1`） | 打印警告而不拒绝（暴露范围由端口映射决定） |

## 4. 无界面调用（不打开浏览器）

按 `docs/integration/automation.md` 执行：

```bash
python -m synwevo_docs.cli apikey create --name 验收 --scopes read,write
export SYNWEVO_DOCS_API_KEY=dw_...
.venv/bin/python tests/integration/automation_example.py \
  --base-url http://127.0.0.1:8765 \
  --source testdata/测试_客户100.xlsx --template testdata/测试_合同模板.docx \
  --definition docs/integration/example-definition.json --out ./out
```

**结果：退出码 0。** 识别 10 条样本 → 预检「有效 10 / 错误 0 / 预计输出 10」→ 任务 `completed`（样本 10、成功 10、失败 0、输出 10）→ 10 个产物逐个 SHA-256 校验通过 → ZIP 370 493 字节 / 11 个条目 → 生成清单 11 行。

抽查产物正文（每份样本取自己的那一行，不是复制示例值）：

```
001.docx  甲方（客户）：示例测试钱磊敏，客户编号 000001。 签约日期：2025年1月4日；合同金额：人民币 1,123.45 元。
002.docx  甲方（客户）：示例测试孙涛强，客户编号 000002。 签约日期：2025年1月7日；合同金额：人民币 1,246.90 元。
003.docx  甲方（客户）：示例测试李桂洋，客户编号 000003。 签约日期：2025年1月10日；合同金额：人民币 1,370.35 元。
```

## 5. 界面与接口结果一致

同一个项目（界面 E2E 建的 `UI验收：客户合同`，100 条样本）：一次任务从界面提交，一次从 `/api/v1` 提交，其余条件不变。

| 检查 | 结果 |
|---|---|
| 产物文件数 | 界面 100 / 接口 100 |
| 文件名集合 | 完全相同 |
| 正文不一致的文件数 | **0** |
| 字节完全相同的文件数 | **100 / 100** |

本次对比中连非业务元数据也一致，因此没有需要说明的差异项。

## 6. 权限、输入与接口契约

| 检查 | 结果 |
|---|---|
| 无密钥访问 `/api/v1` | 401 `unauthorized`，响应头 `x-request-id` 与响应体 `request_id` 一致 |
| 错误密钥 | 401 `unauthorized` |
| 调用方自带 `X-Request-ID` | 原样回显到响应头与响应体 |
| 只读密钥写操作 | 403 `forbidden`（`tests/test_api_v1.py`） |
| 其他租户访问 | 404（不泄露资源是否存在，`tests/test_api_v1.py`） |
| 不存在的项目 | 404 `not_found` |
| 非法样本规则 | 400，消息「未知的样本模式」 |
| 越界本地路径 `/etc/passwd` | 400 `path_not_allowed` |
| 不兼容定义版本 | 400 `invalid_definition` |

## 7. 幂等、并发、取消与计数核对

| 检查 | 结果 |
|---|---|
| 4 线程并发提交同一幂等键、同一请求 | 4 个 202，**同一个任务 ID** |
| 同键 + 不同请求 | 409 `idempotency_conflict` |
| 计数核对 | `documents + failed + skipped + cancelled == samples`（100 = 100） |
| 对终态任务取消 | 200，`cancel={"accepted":false,"reason":"already_terminal"}`，终态不变 |
| 重复取消 | 响应完全相同 |
| 取消中 / 租约恢复 / 重试语义 | `tests/test_job_reliability.py`（10 项），含中断工作进程后恢复仍产出 5 份且不重复 |

## 8. 事件与 Webhook（真实进程，非模拟）

独立实例（`SYNWEVO_PORT=8788`，独立数据目录）+ 一个会校验签名的本地接收端。

| 检查 | 结果 |
|---|---|
| 默认拒绝 `http://` 回调 | 「回调默认要求 https；确需 http 时设置 `SYNWEVO_DOCS_WEBHOOK_ALLOW_HTTP=1`」 |
| 默认拒绝内网地址 | `https://127.0.0.1:8877` → 「解析到内网地址 127.0.0.1；请加入 `SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE`」 |
| 云元数据地址 `169.254.169.254` | 即使配置了 `127.0.0.0/8` 允许列表仍被拒绝（允许列表只放行明确网段） |
| 配置允许列表后投递 | 收到 `job.completed`，HMAC 签名校验 **通过**；伪造签名被接收端拒绝 |
| 接收端返回 503 | 30 秒后按退避重试，第二次 200；两次的 `event_id` 与 `delivery` **相同**（接收端可据此去重） |
| 投递记录 | `webhook_deliveries`：`status=delivered, attempts=2, last_code=200` |
| 回调失败期间产物 | 全程可查询与下载（自动化脚本在首次 503 之后仍成功下载全部产物） |
| 事件序列 | `job.created → job.started → job.completed` |

## 9. 浏览器端到端

```bash
.venv/bin/python apps/docs/scripts/ui_e2e.py           # 13.6s 通过
.venv/bin/python apps/docs/scripts/ui_e2e_more.py      # 10.2s 通过（4 条流程）
.venv/bin/python apps/docs/scripts/ui_e2e_keyboard.py  #  4.1s 通过（4 条流程）
```

`ui_e2e_keyboard.py` 覆盖本轮新增的交互，全部断言针对真实状态：

| 流程 | 断言 |
|---|---|
| E 命令面板 | `⌘K` 打开、输入框自动获得焦点、按中文过滤、Esc 关闭；「查看模板字段」回车后侧栏真的切到字段页签；`?` 打开快捷键表 |
| F 列表键盘与批量 | 点击条目后列表获得焦点；J/X 两次后 `aria-selected` 为 `[false,true,true]`、批量条「已选 2 / 3」；`⌘A` 全选且 `getSelection()` 长度为 0；`D` 后服务端 `enabled` 全 false、备注「禁用 3 个映射（#1、#2、#3）」、`rev` +1；`⌘Z` 后全部恢复且 `rev` 回到原值 |
| G 主题与纸张 | 深色下外壳颜色改变，`.doc-page` 仍为 `rgb(255,255,255)`、`.gcell` 与浅色完全相同 |
| H 布局与动效 | 拖拽面板宽度后 `getSelection()` 为空；刷新后宽度保留（±2px）；`prefers-reduced-motion: reduce` 下 `--dur-2` 为 `0ms` |

该套件验证过「断言写错时会失败」：把「已选 2」改成「已选 9」后报错并打印真实的 aria 快照。

浏览器控制台：三套 E2E 均注册了 `pageerror` 监听并在结束时断言为空，运行期间**没有未处理异常**。

## 10. 持续使用（资源）

连续 6 轮「新建项目 → 上传 → 建模板与字段 → 规则 → 映射 → 预检 → 生成 10 份 → 下载 ZIP」：

| 轮次 | 状态 | 服务端 RSS | 打开的文件句柄 | 临时文件 |
|---|---|---|---|---|
| 起始 | — | 465 MB | 93 | 0 |
| 1–5 | completed ×5 | 465 → 466 MB | 93 | 0 |
| 6 | completed | 467 MB | 93 | 0 |

6 轮后 `/readyz` 仍为 `{"database":"ok","storage":"ok","worker":"ok"}`。未出现内存持续增长、句柄泄漏、临时文件堆积或任务积压。

## 11. 性能实测

两种测量方式，都注明方法：

* **帧时间与交互到绘制**：`scripts/ui_perf.py`，驱动一个**可见**的 Chrome 窗口（headless 或隐藏窗口会把 `requestAnimationFrame` 节流，测到的是节流值而不是应用的表现）。本机显示器空闲节拍实测中位 **8.3 ms**（约 120 Hz），下表的帧时间应与它对照看。
* **交互项**的数字是「事件派发 → 等待两帧 → 结束」，因此 16.6 ms ≈ 2 × 8.3 ms 的显示节拍，说明处理与绘制在一帧内完成，而不是耗时 16.6 ms。均**不含**操作系统输入延迟。

可见窗口实测（`scripts/ui_perf.py`，Chrome 152，1440×900）：

| 指标 | 100 行 × 5 列项目 | 20000 行 × 30 列 / 3.5 MB |
|---|---|---|
| 首个可操作视图（导航 → 网格出现） | 94 ms | 916 ms |
| 显示器空闲帧间隔（对照） | 中位 8.3 ms，p95 9.6 | 中位 8.3 ms，p95 9.8 |
| **滚动时帧间隔** | 中位 8.3 ms，p95 10.0，最大 16.6（n=229） | 中位 8.3 ms，p95 9.7，最大 16.6（n=208） |
| **拖拽分隔条时帧间隔** | 中位 8.3 ms，p95 9.3，最大 10.3（n=120） | 中位 8.3 ms，p95 8.6，最大 10.2（n=119） |
| 单元格选区（两帧口径） | 中位 16.6 ms，p95 16.8 | 中位 16.6 ms，p95 18.3 |
| 映射列表键盘移动（联动双侧高亮，两帧口径） | 中位 16.7 ms，p95 17.7 | —（该项目无映射） |
| 长任务（>50 ms） | **0 个** | **0 个** |
| 渲染的单元格节点 | 170 | 204（总行高 440 044 px） |
| JS 堆（前 → 后） | 7 → 8 MB | 6 → 11 MB |
| 页面异常 | 无 | 无 |

即：滚动与拖拽的帧间隔中位数等于显示器自身的节拍，滚动全程最多出现一次 16.6 ms（掉一帧），拖拽没有掉帧，且没有任何长任务。

服务端与接口：

| 指标 | 结果 |
|---|---|
| 首屏（100 行项目）DOMContentLoaded | 32 ms |
| 20000×30 / 3.5 MB 表上传+解析 | 6.63 s |
| 该表首个网格窗口 / 其后每页 | 380 ms / **11–12 ms**（修复前 400–650 ms） |
| 打开该文件的接口流量 | **136 KB**（修复前 269 KB） |
| 校验 / 样本 / 预览 / 定位接口 | 8 / 4 / 29 / 2 ms |

批量生成（`scripts/benchmark.py`，走真实处理链路，峰值 RSS 452 MB）：

| 场景 | 结果 |
|---|---|
| XLSX 100 行 → DOCX | 1.21 s（12.1 ms/份） |
| XLSX 1 000 行 → DOCX | 11.12 s（11.1 ms/份） |
| XLSX 5 000 行 → DOCX | 65.35 s（13.1 ms/份） |
| XLSX 20 行 → DOCX + PDF | 14.2 s（355 ms/份，主要是 LibreOffice 转换） |
| 2 000 条明细 / 200 组 → XLSX | 1.0 s（5.0 ms/份） |
| PDF 模板 300 份 | 22.97 s（76.6 ms/份） |
| OCR 一张 1240×700 扫描图 | 0.21 s |

「关键交互反馈约 100 ms 内」与「滚动、拖拽接近屏幕刷新节奏」两个目标在上述**已测项目**上达成。这些是本机（Apple Silicon、120 Hz）实测值，不代表其他机器、其他刷新率或更大规模。

## 12. 六项已知限制的逐项核验（2026-09-23 本轮）

用户提出的限制列表来自旧版 README。逐项用真实文件核验后的结论：

| 限制原文 | 核验结论 | 证据 |
|---|---|---|
| 「软件不计算公式」 | **不成立**：内置引擎实际计算并写入缓存值 | 真实链路生成后用 `openpyxl(data_only=True)` 读回：`=B2*C2`→1194、`=ROUND(D2*1.13,2)`→1349.22、嵌套 `IF`→「大客户」、`SUM`→1194、`TEXT(...)&`→「1,194.00 元」、`IFERROR`→99.5，且第二份样本取到自己的值（60 / 67.8 / 「小额」） |
| 「新内容更长时分页可能变化」 | **边界属实，但已可检测并自动处理** | 一页模板 + 超长内容实测：`layout=off` 静默出 2 页；`layout=warn` 生成并报 `page_overflow` 警告「输出有 2 页，模板为 1 页」；`layout=fit` 该样本**判为失败不产出**（任务 `completed_with_errors`，1 成功 1 失败）；字段勾选「允许缩小」后 `fit` 缩到 **68%** 并成功放回 1 页，带 `shrunk_to_fit` 警告。每一级都由 LibreOffice 真实排版核对，不是估算 |
| 「PDF 模板只支持单值文本框，没有重复明细」 | **不成立**：PDF 重复明细区域已实现 | `tests/test_pdf_repeat.py` 断言列识别（`序号/品名/数量/单价/金额`）、行高、可容纳行数、超出策略 |
| 「尚未实现：图片模板、非表格内容块重复、单文件多表单块切分」 | **三项均已实现并有测试** | `test_image_template.py`（输出同尺寸 PNG/JPEG，保留 DPI/ICC/EXIF/透明通道，字段区外像素逐点相同）、`test_docx_blocks.py`（重复内容块）、`test_per_block.py`（XLSX/DOCX/PDF/TXT 四种格式的每块一份）。五个文件合计 **65 passed** |
| 「PyMuPDF 是 AGPL」 | **已有可部署的无 AGPL 方案，实测通过** | 用导入拦截器让 `import pymupdf` 真正失败：服务正常启动、`/readyz` ok、能力矩阵报 `pdf_engine=pdfium`（Apache-2.0/BSD-3）、无界面流程 10/10 成功、PDF 来源解析与中文词坐标/查找/渲染全部正常、PDF 与图片**模板**返回 `engine_unavailable` 并说明原因。仍用 PyMuPDF 时，`/api/source-code` 提供 AGPL 对应源代码（实测 479 296 字节 / 153 条目，含 `SOURCE_OFFER.txt`，不含 `data/`、`.venv/`、`node_modules/`）。详见 `docs/DEPENDENCIES.md` 第 6 节 |
| 「全部用合成数据验收」 | **属实，仍是外部阻碍** | 未取得脱敏真实文件；界面「保存为回归用例」可在拿到样本后直接补充回归集 |

核验过程中按同一思路排查「枚举值静默回落」这一缺陷类，发现并修复三处真实缺陷：DW-049（`layout`）、DW-050（字段 `empty_policy` 等）、**DW-051（合并方式静默改为拼接，属错填数据）**。同时确认 `aggregate()`、`transforms.apply/validate`、样本 `mode`、`error_policy`、`formats` 已正确校验，不存在同类问题。

## 13. 可访问性与主题

| 检查 | 结果 |
|---|---|
| 键盘完成映射浏览、多选、启用/禁用、删除、撤销 | 通过（`ui_e2e_keyboard.py` F） |
| 焦点管理：点击条目后列表可直接用键盘 | 通过（断言 `document.activeElement` 为 `.maplist`） |
| 非颜色提示 | 选中有勾选标记与 `aria-selected`，禁用有「已禁用」徽章，错误有文字说明 |
| 语义角色 | 映射列表 `listbox`/`option`/`aria-multiselectable`，批量条 `toolbar`，命令面板 `dialog`+`listbox` |
| 减少动态效果 | `prefers-reduced-motion: reduce` 下 `--dur-2` = `0ms`（实测） |
| 深色主题不影响文档 | `.doc-page`、`.gcell` 在深浅色下均为 `rgb(255,255,255)` / `rgb(17,20,24)`（实测） |
| 中文输入法 | 全局按键处理检查 `isComposing` / `keyCode 229`；输入框内不触发字母快捷键 |

## 14. 未覆盖 / 未测量（如实记录）

| 项 | 原因 | 影响 |
|---|---|---|
| Microsoft Word / Excel / PowerPoint 实机打开 | 自动化链路用 LibreOffice 打开与渲染；Office 需人工或桌面自动化 | 见 `docs/ACCEPTANCE.md` 第 13 项（上一轮人工检查记录） |
| 真实业务样本 | 未获得用户的（脱敏）真实文件 | 全部验收基于合成数据；真实文件的结构差异未覆盖。界面「保存为回归用例」可在拿到样本后直接补充 |
| 除 Chrome 外的浏览器 | 未运行 | Firefox / Safari / Edge **未测试**，不声称兼容 |
| 其他刷新率 / 低配机器上的帧时间 | 只在本机 120 Hz 屏上测过 | 60 Hz 或更慢的机器表现未知 |
| 多实例共享数据目录的长时间并发 | 只做了同进程 4 线程并发与单次中断恢复 | 多进程部署的长稳未验证 |
| 大规模并发负载（多客户端同时提交） | 未运行 | 上表所有数字均为单客户端、单进程 |

## 16. 2026-09-25 导航、弹层与移动端轮

### 16.1 隔离实例（浏览器证据的来源）

三服务隔离实例 `SYNWEVO_DATA_DIR=<scratchpad>/live/data SYNWEVO_TEST_INSTANCE=1 SYNWEVO_MAIL_MODE=file …`，端口
31770/31769/31765，本机 OpenID 替身 31780（`services/identity/scripts/idp_double.py`）。`/healthz` 报告
`instance=test`；两套浏览器脚本都拒绝驾驭非测试实例。**没有任何测试指向 `data/`。**

### 16.2 浏览器回归（Chromium = 本机 Google Chrome，WebKit = Playwright 构建）

```bash
.venv/bin/python services/identity/scripts/ui_e2e_shell.py http://accounts.localhost:31770 \
    http://portal.localhost:31769 http://docweave.localhost:31765 <受控账户> <口令> --webkit --webkit-accounts=http://127.0.0.1:31770
.venv/bin/python apps/docs/scripts/ui_e2e_mobile.py          # 自起隔离实例，390×844 触摸模拟
```

| 检查（每项在 1280×720 与 375×740 各跑一次） | Chromium | WebKit |
|---|---|---|
| 账户页顶栏「← 返回工作空间」指向服务端 `return_targets` 列出的源；375px 出现「菜单」→ 抽屉含账户导航与退出，Esc 关闭 | 通过 | 通过 |
| 账户子页冷启动 `/api/auth/state`=1、`/api/auth/me`=1；随后触发 `focus`+`visibilitychange` → 0 次请求 | 通过 | 通过 |
| 成员列表末行「操作」菜单：5 个菜单项全部落在视口内；Esc 关闭并把焦点还给按钮；375px 下 `td.act` 为 `sticky` | 通过 | 通过 |
| 门户仪表盘触发 `focus`+`visibilitychange` 后无 `.pt-loader`、仪表盘仍挂载；已登录冷启动 `/workspace` 的 API 请求恰为 `home×1 + me×1 + state×1`（此前一版为 `home×2`） | 通过 | 通过（CI-08 起，经本地代理） |
| 公开首页对已登录访客：加载期间每 50 ms 采样顶栏，只出现「進入工作空間」，从未出现「登录/登入」 | 通过 | 通过（CI-08 起） |
| Docs 工作台 375px：「文件/来源/目标/映射」四页签；点「目标」后来源面板 `display:none`、选择栏可见；1280px 页签隐藏 | 通过 | 通过（CI-08 起） |
| Docs 手机主链路：新建→上传 XLSX→上传 DOCX→4 字段→样本→4 映射（跨面板切换）→预览→生成 100 份→下载 ZIP 读回；无横向滚动；主按钮 ≥ 40px 且在首屏 | 通过，11.8 s | 通过，12.1 s（`E2E_ENGINE=webkit`，CI-09 修复后） |
| 管理员分阶段认证（隔离实例的独立管理员账户，scratchpad 脚本）：口令登录 `amr=password` 且有权限；后台「登录方式」3 张卡（Google/Microsoft 未配置、Test IdP 已启用，连接检查「可达」），页面源码不含 secret 与完整 client id；邮箱验证码登录 → `amr=email`、`permissions=[]`、`admin_locked=true`、`/api/admin/overview` 403；`/admin` 显示提示与「前往账户页确认身份」；账户页 `#step-up`：错口令 →「口令不正确。」，正确口令 → `amr=email+password`、权限恢复、「已确认」提示；`/admin` 8 个页签 | 通过 | 未跑 |

WebKit 通过系统解析器解析主机名，本机没有 `*.localhost` 记录。CI-08 起脚本内置一个本地 HTTP 正向代理
（`services/identity/scripts/localhost_proxy.py`：把 `*.localhost` 解析到回环地址，CONNECT 也做隧道），WebKit 上下文经它访问三个源，
浏览器侧每个源仍是独立的 host-only Cookie 罐，跨源单点登录真实发生；因此 WebKit 现在与 Chromium 覆盖相同的页面。截图：`docs/screenshots/shell-*.png`（含 `shell-admin-identity-providers.png`、`shell-admin-step-up-prompt.png`、`shell-account-step-up.png`）、`2x_手机_*.png`。

**这不是真机验证。** 两套脚本证明的是 Chromium/WebKit 引擎下的布局与行为；iOS Safari（工具栏收缩、软键盘、
`100dvh`）、Android Chrome、系统字体缩放 200% 需要真机清单：

| 真机项 | 做什么 | 通过条件 |
|---|---|---|
| 账户页返回 | 手机从门户进账户页，再点「返回工作空间」 | 回到门户仪表盘，无中间登录页 |
| 抽屉 | 打开菜单，切换语言，点「退出登录」 | 背景不可滚动；退出后落到「已退出」页 |
| 成员菜单 | 在 5 人以上的空间打开末行菜单 | 菜单完整、可点；键盘弹出时不遮住输入框 |
| Docs 主链路 | 上传→映射→预览→生成→下载 | 下载在系统文件应用可打开 |
| 登录/MFA/Passkey | 邮箱验证码粘贴、TOTP 输入、平台认证器 Passkey | 验证码框不被键盘遮挡；Passkey 用生物特征完成 |

### 16.3 后端与前端测试

```bash
.venv/bin/python -m pytest                       # 三个套件（结果见下）
.venv/bin/python scripts/check_boundaries.py     # 通过
.venv/bin/python scripts/check_brand.py          # 通过
.venv/bin/python scripts/check_design.py         # 通过（一处字面阴影色改为 var(--shadow) 后）
npm run typecheck && npm run build               # 三外壳通过
cd packages/ui && npm test                       # 16 项（含 layer.test.ts 5 项）
```

新增：`services/identity/tests/test_session_strength.py`（12）、`test_identity_provider_status.py`（3）、
`tests/integration/test_return_targets.py`（4）；`test_email_code_login.py` 与 `test_external_identity.py` 的
管理员用例改为「一因素 → 403 step_up_required → 口令 → amr 升级 → 200」。

全量结果（2026-09-25，仓库根目录 `.venv/bin/python -m pytest`，8 分 47 秒）：**1964 passed / 2 failed / 1 skipped**。
两处失败都在本轮新增内容上，修正后重跑对应模块 51 项通过，整体再未变动其他代码：

| 失败 | 原因 | 处理 |
|---|---|---|
| `test_platform_tooling.py::test_the_repository_itself_passes` | 新脚本 `ui_e2e_mobile.py` 用了 `sys.path.insert`，被边界守卫拒绝 | 去掉插入：脚本目录本来就在导入路径首位 |
| `test_portal_public.py::…only_platform_admin_api_permission` | 夹具用 `amr="fixture"` 造会话，在新策略下是"未知的单一因素"，管理员被锁 | 夹具改为 `amr="password"`（它模拟的正是口令 SSO 登录），并注明原因 |

1 项 skipped 是既有的 LICENSE 检查，与本轮无关。

### 16.5 回流与触控目标审计（持续迭代 CI-01，2026-09-25）

**依据**（访问日期 2026-09-25）：WCAG 2.2 SC 1.4.10 Reflow（AA）——纵向滚动内容在 320 CSS px 宽度下不得要求二维滚动，
数据表格等本质二维的内容例外但其表头/搜索/分页仍须回流（w3.org/WAI/WCAG22/Understanding/reflow）；SC 2.5.8 Target Size (Minimum)（AA）
——指针目标至少 24×24 CSS px，句中内联链接例外（…/target-size-minimum）；SC 2.5.5（AAA）44×44 作为主要操作的增强目标；
Fluent 2 Layout：断点 small 320–479 / medium 480–639 / large 640–1023 / x-large ≥1024，触控目标 iOS 与 Web 44×44、Android 48×48
（fluent2.microsoft.design/layout）。这些是可核查的官方规范，不是从竞品界面推断。

**工具**：`services/identity/scripts/ui_mobile_audit.py`——Chromium 手机模拟下逐页测 `scrollWidth`、越出视口的可见元素（允许
`.list-wrap`/`.account-table`/表格画布等自身滚动容器）、所有指针目标的最小边（<24 报告）、主要操作（<44 报告为信息）。只驾驭
`instance=test` 实例。页面：登录/注册/找回/邀请/404、账户/组织/引导/成员/邀请、后台 8 页签、API 台、门户首页与仪表盘、Docs 首页与工作台。

```bash
.venv/bin/python services/identity/scripts/ui_mobile_audit.py http://accounts.localhost:31770 \
    http://portal.localhost:31769 http://docweave.localhost:31765 <管理员账户> <口令> --widths=320,375,768
```

| | 修复前 | 修复后 |
|---|---|---|
| 页面级横向滚动（`scrollWidth > innerWidth`） | 0 页 | 0 页 |
| 越出视口的元素（320 px） | 后台「登录方式」卡片右缘 325 px（`minmax(300px,1fr)`） | 0 |
| 目标 < 24 px 的页面（3 个宽度合计） | 30 页次：`a.authfoot`「忘记口令？/返回登录」、邀请页「前往登录」、`.org-actions a`、门户 `.pt-link`/`.pt-text-action`（20–22 px）、`label.check` 单选/复选行（19–20 px）、头像 `input[type=file]`（20 px） | 0 |
| 主要操作 < 44 px（粗指针，信息项） | `.btn.sm.primary` 40 px；后台页签 42 px 宽 × 44 px 高 | `.btn.primary` 与 `submit` 在粗指针下 44 px；页签宽度按文字（高 44）保留 |

修复都在共享层：`label.check` 最小 24 px（粗指针 32）；独立操作链接（`a.authfoot`、`.org-actions a`、`.action-link`、门户 `.pt-link`/
`.pt-text-action`）改为 `inline-flex` + 最小高 24（粗指针 32）；`input[type=file]` 24/40；`.btn.primary` 与提交按钮在粗指针下 44；
`.idp-grid` 用 `minmax(min(300px,100%),1fr)`。账户页会话表本来就在 `.account-table { overflow-x:auto }` 内滚动，审计把它列为
允许的二维容器（首轮的 8 处"越界"是审计未识别该容器，非页面缺陷）。三个宽度重跑：**0 页有回流或目标尺寸发现**。

**内容态补审（CI-04）**：首轮只审到空态。给隔离实例的 Bob 建了一个组织（长空间名）与 3 条待处理邀请后重审
`/organizations/<oid>`、成员与邀请页、Docs 首页项目表与工作台：发现组织页「返回我的组织」链接 20 px、Docs 首页项目表在 320/375 px
越出（`table.tbl` 无滚动容器）、工作台选择栏提示按桌面 32%/28% 截断而越出。修复：`.action-link`、共享 `.tbl-wrap`（首页项目/模板表）、
≤900 px 选择栏提示占满一行并换行。审计同时学会忽略被 `overflow: hidden` 祖先裁切的元素（此前把被截断的提示算成越出），
`<44 px` 只对 `.primary` 报告。Bob（含组织/邀请/项目内容）与管理员两组、三个宽度重跑：**0 发现**。

**规模补审**：把 Bob 组织空间的待处理邀请增至 50 条后重审成员/邀请页（320/375 px）：0 发现（sticky 选择列/操作列、批量条贴底在 50 行下成立）。

**高密度屏**：`ui_e2e.py` 与 `ui_e2e_more.py` 新增 `E2E_DPR` 环境变量；在 DPR 2 与 3 下重跑（PDF 模板鼠标框选、网格单元格命中、
文字选区→字段、拖拽区域→明细映射）全部通过——选区与实际取数的坐标映射不依赖设备像素比（§六「选择与坐标」）。

**对比度（CI-07）**：审计新增 WCAG 2.2 SC 1.4.3 采样——每个可见文本节点的前景色对合成后的实际背景（沿祖先合成半透明背景），
正文 4.5:1、大字（≥24 px 或 ≥18.66 px 粗体）3:1，禁用控件除外。采样器先在合成页面上验证（#9ca3af 14 px 报 2.54:1、
#94a3b8 于 #eff6ff 报 2.36:1、#8a8a8a 26 px 大字 3.03:1 不报、禁用按钮不报）。Bob 与管理员两组、375/1280 px、共 30 余页：
**0 处低于阈值**——令牌 `--text-2 #475569`（7.6:1）与 `--muted` 在白/浅蓝底上都达标。

这仍是 Chromium 引擎测量，不是真机；真机项见 16.2。

### 16.6 HTTP 安全头（持续迭代 CI-02，2026-09-25）

**基线（线上匿名 `curl -I`，2026-09-25）**：`accounts.` / `synwevo.` / `docwave.wegreen.ltd` 的 HTML 与 JSON 响应只有
`X-Frame-Options: SAMEORIGIN`、`Referrer-Policy: no-referrer`（API 另有 `Cache-Control: no-store`）；没有 `Strict-Transport-Security`、
`X-Content-Type-Options`、`Content-Security-Policy`、`Permissions-Policy`、COOP/CORP。

**依据**（访问日期 2026-09-25）：OWASP ASVS 4.0.3 V14.4.1–14.4.7（Content-Type 字符集、CSP、nosniff、HSTS、Referrer-Policy、
frame-ancestors；github.com/OWASP/ASVS v4.0.3 `0x22-V14-Config.md`）；OWASP HTTP Headers Cheat Sheet 的推荐值
（`nosniff`、HSTS、`Cross-Origin-Opener-Policy: same-origin`、`Cross-Origin-Resource-Policy: same-site`、`Permissions-Policy`、
不再使用 `X-XSS-Protection`/Expect-CT/HPKP）。14.4.2（API 响应 `Content-Disposition: attachment`）未采用：与浏览器内 JSON 查看和
现有 SDK 行为冲突，`nosniff` + 正确 Content-Type 已覆盖其针对的嗅探风险；记录为有意的偏差。

**实现**：`synwevo_core.http.apply_security_headers`——所有三个角色的每个响应（含 401/404/网关拒绝）。CSP 为强制模式，策略依据
三个外壳的实际资源清单确定：Vite 构建无内联脚本（dist `index.html` 0 个内联 `<script>`）、无第三方资源、无 WebAssembly/eval
（bundle grep 为 0），React 内联 `style=` 需要 `style-src 'unsafe-inline'`，favicon 为 `data:`，Docs 查看器/头像预览用 `blob:`，
17 个表单全部经 fetch 提交（无 `action=`）。HSTS 仅在 TLS（直接或 `X-Forwarded-Proto: https`）时发出，`max-age=31536000; includeSubDomains`，
不加 `preload`（非顶级域）。`SYNWEVO_CSP` 可替换/关闭，作为无需构建的临时措施（operations.md §4.0）。

**验证**：`tests/integration/test_security_headers.py` 6 项（三角色、HTML/JSON/404、HSTS 条件、覆盖开关、setdefault 不覆盖路由自设头）；
五套浏览器脚本都新增 CSP 违规捕获并断言为 0：`ui_e2e_shell.py`（Chromium 桌面/手机 + WebKit 账户源）、`ui_e2e.py`（两次）、
`ui_e2e_more.py`、`ui_e2e_keyboard.py`、`ui_e2e_mobile.py` 在强制 CSP 下全部通过、0 违规。隔离实例 `curl` 三个源均带全部头。

### 16.7 映射对话框语义回归（CI-03，顺带发现）

`ui_e2e_more.py` 自 2026-09-24 后未再运行；本轮重跑发现明细表映射对话框对所有 `detail` 提案一律显示「◆ 随文件变化：每个来源文件在
相同位置取值」，而服务端 `locators.py` 对每组一份模式的语义是「每份输出写入当前分组的全部明细行」——4caea87 起客户端改用
`describeLocator` 后丢掉了服务端标签的解释。修复：对话框按 `record_rule.mode` 给明细提案专门的三语言说明（每组一份 / 每行一份 /
每文件一份）；脚本断言改为当前正确文案；`ui_e2e.py` 删除字段后的重建增加等待恢复文本（此前偶发竞态）。

### 16.8 批量生成性能基线复测（2026-09-25，同一台 Apple Silicon，`apps/docs/scripts/benchmark.py`）

与 §11 记录的 2026-09-23 基线对照（真实处理链路：解析 → 记录 → 计划 → 填充 → 校验 → 存储；合成数据；单进程）：

| 场景 | 2026-09-23 | 2026-09-25 |
|---|---|---|
| XLSX 100 行 → DOCX | 1.21 s（12.1 ms/份） | 1.19 s（11.9 ms/份） |
| XLSX 1 000 行 → DOCX | 11.12 s（11.1） | 11.10 s（11.1） |
| XLSX 5 000 行 → DOCX | 65.35 s（13.1） | 56.39 s（11.3） |
| XLSX 20 行 → DOCX + PDF（LibreOffice） | 14.2 s（355） | 15.83 s（396） |
| 2 000 条明细 / 200 组 → XLSX | 1.0 s（5.0） | 0.83 s（4.1） |
| PDF 模板 300 份 | 22.97 s（76.6） | 22.88 s（76.3） |
| OCR 1240×700 | 0.21 s | 0.26 s |
| 峰值 RSS | 452 MB | 407.5 MB |

两天之间加入了统一网关、会话/授权检查、安全头等中间件，吞吐没有退化（5 000 行一档更快，PDF 转换在 LibreOffice 抖动范围内）。
这是回归检查点，不是性能改进声明；数值随机器与并发变化。

### 16.9 共享菜单与抽屉的键盘模式（CI-05）

**依据**：WAI-ARIA APG「Menu Button」与「Dialog (Modal)」模式（Enter/Space 打开并聚焦首项，方向键移动且首尾回绕，Home/End，Esc 关闭并把焦点
还给触发按钮；对话框内 Tab 不离开、关闭后焦点回到打开者）；WCAG 2.2 SC 2.1.1 键盘、2.4.3 焦点顺序。

`ui_e2e_shell.py` 新增：在有 ≥2 个可用项的行菜单上用 Enter 打开、ArrowDown/End/ArrowDown（回绕）/ArrowUp（回绕）/Home、Esc 后焦点回到
按钮；手机抽屉内连按 12 次 Tab 焦点不离开、Esc 关闭后焦点回到「菜单」按钮。

首次运行在 WebKit 暴露两处真实缺陷（Chromium 不出现）：

| 缺陷 | 根因 | 修复 |
|---|---|---|
| 抽屉内 Tab 从 `<select>` 跳出对话框 | 焦点陷阱只在首尾两端接管，中间交给浏览器；Safari 默认 Tab 跳过按钮与链接，于是从中间直接跳出 | `useModalFocus` 每次 Tab 都自行按 DOM 顺序移动（`trapIndex`），Safari 用户也能用键盘到达按钮和链接 |
| Esc 关闭抽屉后焦点不回到按钮 | WebKit 点击不聚焦按钮，打开时 `document.activeElement` 是 `<body>`，无处可回 | `a11y.lastPointerTarget()` 记录指针最后按下的可聚焦元素；对话框在 `activeElement` 为 body 时回到它 |

修复后 Chromium 与 WebKit 的桌面/手机四组全部通过；Docs 三套桌面/手机 E2E 与键盘套件复跑通过。

### 16.10 WebKit 全覆盖与它找到的手机缺陷（CI-08、CI-09）

**CI-08**：`localhost_proxy.py` 让 Playwright WebKit 访问 `*.localhost` 三个源（见 16.2 说明）；`ui_e2e_mobile.py` 增加 `E2E_ENGINE=webkit`。
WebKit 现在跑完与 Chromium 相同的外壳检查（账户/门户/Docs、桌面与手机、键盘、CSP）和 Docs 手机主链路。

三套桌面 Docs 套件也加了 `E2E_ENGINE=webkit`：`ui_e2e.py`、`ui_e2e_keyboard.py` 直接通过；`ui_e2e_more.py` 的表格编辑器流程里
两处「原生 Tab 离开网格后焦点落在 Sheet1 页签按钮」的断言在 WebKit 不成立——网格有意不截获 Tab（无键盘陷阱，WCAG 2.1.2），
而 Safari 默认 Tab 跳过按钮与链接，这是浏览器策略不是产品缺陷；断言改为引擎无关的「编辑器已关闭、焦点离开网格、选区未移动」，
WebKit 下通过。

**CI-09**：WebKit 手机主链路首次运行在「确认样本规则」处卡住。截图（`docs/screenshots/25_手机_样本规则_失败.png`）显示原因：
底部的提示条（toast「已导入模板…」）正好盖住对话框底栏的主按钮，点击落在提示条上。Chromium 只是因为时序不同没碰上。
修复在共享样式：≤620 px 时提示条改在顶部（安全区之下、整行宽），容器 `pointer-events: none` 只有提示条本身可点。
修复后 WebKit 与 Chromium 手机主链路均通过；外壳回归、Docs 三套桌面套件与 375 px 审计复跑通过。

### 16.11 账户外壳按路由拆分（CI-10）

**问题与基线**：账户外壳是单一包——登录页也要先下载平台管理、API 台、成员治理、MFA 二维码渲染等全部代码。隔离实例实测
（Chromium 手机模拟，`/login`）：**709.7 KB JavaScript（gzip 219 KB）**，`/register`、`/account` 相同。

**依据**（访问 2026-09-25）：web.dev「Reduce JavaScript payloads with code splitting」——"Split your bundle into multiple pieces and only
send what's necessary at the very beginning"，按路由/组件懒加载减少解析、编译与执行的启动成本，改善 INP；Docs 外壳已用同一做法
（`DocEditor`/`SheetEditor` 懒加载）。

**实现**：`services/identity/web/src/main.tsx` 只保留登录页与两个状态页在入口块，其余 13 个路由 `lazy()`；`Suspense` 回退为与各页
一致的「加载中…」状态（三语言）。发布后旧标签页里的 HTML 仍指向上一版块名时，块加载失败会**只重载一次**（sessionStorage 记路径），
第二次失败照常报错，不会循环。

**结果**（同一实测方法）：

| 路由 | 修复前 | 修复后 |
|---|---|---|
| `/login` | 709.7 KB（1 文件） | **380.7 KB**（入口 302 KB + 共享 86 KB + 运行时；gzip ≈ 125 KB） |
| `/register` | 709.7 KB | 386.0 KB（+ Register 5 KB） |
| `/forgot-password` | 709.7 KB | 384.3 KB（+ Recover 4 KB） |
| 其他页面 | — | 进入时加载各自的块：Account 62 KB、Members 38 KB、Admin 36 KB、ApiPlatform 65 KB、Organizations 21 KB（原始大小） |

入口块减少 46%（gzip 约 43%）。验证：账户外壳 19 项校验、`test_shells.py`、两引擎外壳回归、375 px 审计；
块失败演练：首次失败 → 恢复并渲染（1 次重载），持续失败 → 不循环。

### 16.12 Docs 外壳按路由拆分（CI-11）

同一做法推广到 Docs：`lazyRoute` 提到 `@synwevo/ui`（账户与 Docs 共用，含块失败一次重载），项目列表留在入口，工作台、任务页、
内置编辑器、回归/能力/关于页按进入加载。隔离实例实测（Chromium 手机模拟）：

| 路由 | 修复前 | 修复后 |
|---|---|---|
| `/`（项目列表） | 716.6 KB（1 文件） | **488.4 KB**（入口 226 KB + 共享 274 KB + 运行时；−32%） |
| `/p/<id>`（工作台） | 716.6 KB | 701.4 KB（+ Workspace 211 KB 等按需块；与之前基本相同，只是分成 8 个块） |
| `/capabilities` | 716.6 KB | 491.5 KB |

共享块 `perm` 274 KB 仍由列表页加载（两页共用的视图与表现层），是下一步可再拆的对象；本轮不为此再动结构。
验证：四套 Docs 浏览器套件、两引擎外壳回归、单测与守卫。

### 16.13 英文界面残留与顶栏溢出（CI-12、CI-13）

**工具**：审计新增 `--locale=en`——先把账户语言切到英文（`PATCH /api/auth/locale`），再在每页扫描含 CJK 的可见文本节点与
`aria-label`/`placeholder`/`title`，排除调用方声明的夹具内容（组织/空间/项目名）与 `.who/.mono/code`。

**发现**（Bob，英文，1280 px）：
| 文本 | 来源 | 修复 |
|---|---|---|
| 「macOS 上的 Chrome」「未知设备」（设备列表） | 服务端 `describe_client` 把句子拼成中文 | 会话行新增 `client_parts {browser, platform}`（保留 `client` 兼容），账户页用三语言模板拼句 |
| 「Bob Verify 的空间」（个人空间名） | 注册时服务端用中文模板起名，之后是数据 | `PERSONAL_NAME` 按账户语言取模板（`zh-Hant`「…的空間」、`en`「…'s space」）；已有名称不动 |

`test_locale_defaults.py` 10 项（UA → 产品名、三语言默认名、注册/创建/会话行）。复跑 Bob 英文：0 处残留。

**顺带发现（CI-13，与语言无关）**：Docs 首页顶栏在 1280 px 英文下横向溢出 51 px（`scrollWidth 1331`，「＋ New project」被推出窗口），
以及账户有两个空间时顶栏里的空间切换 `<select>` 被压到 21 px 宽（`flex: 0 1 auto` + `min-width: 0`）。修复在共享层：
`.topbar` 任何宽度都允许换行（`flex-wrap: wrap; min-height: 48px`），`PlatformNav` 的切换器 `flex: none; min-width: 140px`。
Bob（375/1280，默认语言）与管理员（375/1280，英文）重审 0 发现；`ui_e2e.py` 与外壳回归通过。

### 16.14 SDK / CLI 与 Node 验收复跑（CI-14）

网关、会话、安全头与路由拆分之后，把机器路径的验收脚本在新鲜隔离实例上复跑（`apps/docs/sdk/acceptance/python_full_flow.py`、
`tests/integration/sdk_acceptance/ts_run.py --builtin-only`；合成数据，真实 HTTP，服务凭据经空间批准）：

| 脚本 | 首次结果 | 根因 | 处理 | 复跑 |
|---|---|---|---|---|
| Node 内置模板 service-auth 冒烟 | 通过（12 + 12 份文档） | — | — | — |
| Python SDK + CLI 全流程 | **失败**：`definition put` 收到 `429 rate_limited`（该流程 1 分钟内约 400 次调用，超出每密钥 300 次/分钟） | SDK 有意不对"非幂等写"重试任何 429（担心中间层转发后才产生的 429）；但 Synwevo 自己的 `rate_limited` 是网关在路由执行前的拒绝，没有副作用 | 两个 SDK 只对 `429 rate_limited` 放行有界重试（含非幂等写，按 Retry-After），其他 429 维持原策略；Python `test_http.py` 与 Node `client.test.mjs` 各增一项（首次 429 后重试成功；持续 429 仍抛错；其他错误码的 429 不重试） | 通过：24 份文档、52 项内置输出读回；期间服务端实际拒绝 4 次（`app-http.jsonl` 中 4 条 429），SDK 全部重试成功 |

依据：Stripe「Rate limits — Handle limiting」（访问 2026-09-25）：对 429 实施带抖动的指数退避重试；Synwevo 网关的 429 在 `_gate`
中间件里于路由之前返回（`packages/core/synwevo_core/http.py`），因此对它重试不会重复副作用。

注意：本机 `.venv` 里的 SDK 是非可编辑安装（`pip install apps/docs/sdk/python`），改 SDK 源码后要重新安装才会被测试与验收脚本用到；
本次首次复跑"通过"的一次是时序未触发限流，不是修复生效，已以重装后的第三次复跑为准。

### 16.15 API 台浏览器验收复跑（CI-15）

`tests/integration/api_platform_browser_acceptance.py`（真实 Chrome，1440×1050，en-US；管理员在 API 台发起凭据申请 → 空间所有者在
账户页批准 → 管理员签发并只在弹层里看一次秘密 → 已安装的 Python SDK 用该凭据经真实 Docs HTTP 完成来源/模板/字段/规则/映射/预检/
原生预览/任务/ZIP/12 份 DOCX 读回 → 轮换与撤销后被拒；门户 SSO 不参与）在 317xx 隔离实例上复跑，**通过**：`page_errors: []`、
`blocked_external_requests: []`。

脚本本身改了两处，都是验收基础设施而非产品：① 原来把 Docs 源硬编码为上一轮批准的 `docweave.localhost:28765`，改为与其他脚本
一致的「`/healthz` 报告 `instance=test` 才驾驭」规则；② 三处 `wait_for_function`/`evaluate` 传的是**表达式字符串**，Playwright 用
eval 求值，被强制 CSP（无 `'unsafe-eval'`）拒绝——改成函数字符串后由 `callFunctionOn` 执行，CSP 保持强制。这也证明 API 台整个
签发流程在强制 CSP 下没有依赖 eval。

### 16.16 API 台各页签内容态审计与主按钮悬停对比度（CI-16）

审计学会点开 API 台的六个页签（授权审批 / 服务凭据 / Webhook / 接口契约 / 调用记录 / 请求调试），并在 16.15 签发了一条合成凭据后
以管理员在 320/375 px 重审：回流、目标尺寸、翻译都为 0，但每个页签都报一处**对比度 2.66:1**——当前页签是 `button.btn.primary`，
指针停在上面时文字色变成深色（`--text`）压在深蓝（`--accent-strong`）上。根因是共享样式：`.btn:hover:not(:disabled)`（三段选择器）
优先级高于 `.btn.primary`（两段），把主按钮悬停时的文字色覆盖回深色；此前只给 `a.btn.primary:hover` 补过，`<button>` 漏了。
**这影响所有外壳里所有 `<button class="btn primary">` 的悬停/按下态**（登录、生成、建立映射、确认等）。修复：`.btn.primary:hover`
与 `:active` 重申 `color: var(--on-accent)`。复核：登录页主按钮悬停 `rgb(255,255,255) on rgb(29,78,216)`（8.6:1）；管理员 API 台
六页签 × 两宽度重审 0 发现。

### 16.17 有映射的工作台与英文长标签（CI-17）

审计在一个**有映射**的项目（16.15 的验收留下的）和英文界面下又找到三处：

| 发现（375/320 px） | 修复 |
|---|---|
| 工作台的映射连线 SVG 覆盖层（`.connectors`）超出视口 4 px | 手机单面板布局没有两侧可连，≤900 px 隐藏覆盖层 |
| 单元格/字段上的映射编号标记 `button.tag.num` 18×18 px | 粗指针下 24×24（SC 2.5.8），鼠标保持 18 px 密度 |
| 账户页英文长链接「Forgot your current password? Reset by email」（`a.btn.ghost`）在 320 px 越出 10 px | ≤620 px 允许按钮文字换行（`white-space: normal; height: auto`） |

Bob 两宽度重审 0 发现；手机主链路与外壳回归通过。另：API 台验收脚本会把两个账户的语言切到英文，重跑审计前已恢复夹具语言。

### 16.18 繁體界面残留检测、整页目标测量与折叠头/页脚链接（CI-18）

**繁體检测**：审计 `--locale=zh-Hant` 时扫描只在简体里出现的常用字（一个约 100 字的集合），命中即为没有 zh-Hant 条目的字符串。
Bob 与管理员两组：产品文案 **0 处**；唯一命中的是用户数据（合成模板里的字段名「客户名称」），已作为夹具内容排除。

**整页测量**：此前目标尺寸与对比度只测首屏可见元素，页脚等折叠区之外的目标没测到。改为整页后新增发现：门户页脚链接
「数据与隐私 / 服务说明」20 px；API 台的折叠头 `<summary>`（「创建订阅」「请求与响应结构」）20 px；API 台页脚链接
「Python SDK / TypeScript SDK / API」20 px（「API」还只有 20 px 宽）。修复在共享层：`summary` 与 `footer a` 最小 24 px
（粗指针 32），页脚链接最小宽 24 px 并居中；门户 `.pt-footer a` 同样。两组账户 320/375/1280 重审 **0 发现**。

审计本身：API 台页签遍历改为只找控制台自己的导航（原选择器也命中顶栏导航，其中唯一的按钮是「退出登录」——在 1280 px 桌面下
点到它就退出了）；语言切换的恢复注册到进程退出，脚本中途失败也会把账户语言改回去。

### 16.19 平台闭环浏览器回归：注册 → 验证 → 引导 → 邀请 → 接受 → 退出（CI-19）

`services/identity/scripts/ui_e2e_onboarding.py`，隔离实例（`instance=test`，文件邮件），手机视口 390×844，
Chromium（安装的 Google Chrome）与 WebKit（Playwright 构建，经本地 `*.localhost` 代理）各走一遍：

```
== chromium (Google Chrome)
  - registered newcomer.af0ae7@example.test → /verify-email
  - e-mail verified, signed in with the chosen password → /onboarding
  - personal workspace chosen → handed over to portal.localhost:31769
  - onboarding done, personal workspace present, status active
  - bob.verify@example.test invited newcomer.af0ae7@example.test through the members page
  - invitation accepted; now a member of w_1072f9c03849
  - signed out → /login; /api/auth/me 401
  - 0 page errors, 0 CSP violations
== webkit (Playwright build; *.localhost through http://127.0.0.1:55416)
  - … 同样八步通过；signed out → /logged-out; /api/auth/me 401
```

脚本像人一样读邮箱：从 `.eml` 取验证链接与邀请链接；验证页按产品规则走两段（先「验证此邮箱」，服务端要求
邮箱持有人设置自己的口令后再「设置新口令并验证邮箱」），验证**不会**自动登录（`verify_email` 拒绝把预注册者的
凭据提升为有效访问），所以脚本随后用新口令登录再进入引导。邀请结果对话框被读取：任何一项「失败」都让脚本失败——
第一次跑就撞上夹具空间 24 小时 50 个邀请的预算（预期限流，`invite_rate_limited`），改用当场新建的团队空间。
两引擎退出登录后的落点不同（`/login` 与 `/logged-out`），两者都是登出成功且 `/api/auth/me` 401；差异记入候选队列核对。

### 16.20 恶意上传防护：EICAR、离线与超限（CI-20）

**协议替身（pytest，8 项通过）**——`apps/docs/tests/test_malware_scan.py` 起一个真实监听 TCP 的 clamd 协议实现，
证明的是集成而不是引擎：

```
apps/docs/tests/test_malware_scan.py ........                             [100%]
```

| 场景 | 观察 |
|---|---|
| 干净 XLSX 上传 | `scanning`（`scan.pending`）→ `ready`，`scan.clean`，`scan.sha256` 等于文件 sha256，下载 200 |
| 含 EICAR 的 CSV | `rejected`，`scan.infected`，签名 `Win.Test.EICAR_HDB-1`，下载 410 `rejected_malware`，blob 已不存在，同字节再上传 400 `malware_detected` |
| 引擎离线 | `scan_failed`（`scan.error`，attempts=1），下载 409 `not_yet_accepted`；引擎恢复后自动重试至 `ready`（attempts≥2） |
| 超过扫描上限 | `scan_failed`（`scan.too_large`），下载 409 |
| 模板 | 干净 200 带 `scan.clean`；EICAR 400 `malware_detected`（`detail.signature`）；离线 503 `scan_unavailable`（`retry_after=30`） |
| 未配置扫描器 | 来源 `parsing` + `scan.not_configured`；模板 `scan.not_configured`——可见的缺失 |

首次推送（`4061731`）的生产流水线在 verify 阶段失败：Docs 外壳的 Node 测试要求 `presentation.ts` 错误目录里每条消息在
`messages.server.ts` 都有英文与繁體资源，五条新错误消息缺资源（本机只跑了 typecheck/build，未跑 `npm test --workspaces`）；
补齐五条三语言资源后 `npm test --workspaces --if-present`（111 项）、`unittest infra/production/tests`（6 项）通过，部署未发生。
Docs 全套件（含 v1 契约与 OpenAPI 定义测试）通过；`tests/integration/test_migration_v7.py` 改为断言当前 schema
（v8）并检查两张表的 `scan` 列；账户服务与集成套件其余全部通过。SDK：Python 20 项、Node 7 项通过（等待函数穿过
`scanning`/`scan_failed`）。前端 typecheck 与三外壳构建通过；三项守卫通过。

**真实引擎（本机，ClamAV 1.5.4，Homebrew；官方 Docker 镜像只有 amd64，在这台 Apple Silicon 上拉取 30 分钟未完成）**——
守护进程用 scratchpad 内的独立配置（`TCPSocket 33100`、`StreamMaxLength 25M`），签名库 `main 63 / daily 28134 / bytecode 339`
由 freshclam 当场下载，`Loaded 3628083 signatures`，常驻 950 MiB。适配器直连探针：

```
ping: True
version: ClamAV 1.5.4/28134/Fri Sep 25 14:25:58 2026
eicar (68 B)                        68 B  infected  sig=Eicar-Test-Signature   0.00s
eicar inside csv (a,b\n + EICAR)     73 B  clean     ← 标准如此：EICAR 只在偏移 0 是签名
eicar inside zip                   184 B  infected  sig=Eicar-Test-Signature   0.00s
clean xlsx fixture                8310 B  clean                                0.04s
clean docx fixture               39521 B  clean                                0.10s
clean pdf fixture                19640 B  clean                                0.11s
26 MiB, policy limit 25 MiB   27262976 B  too_large（未发送，适配器先拒）
26 MiB, policy 64 MiB > daemon 25M        too_large reason='INSTREAM size limit exceeded. ERROR'（守护进程的回复被正确解析）
eicar + 40 spaces (≤128 B)                 infected  sig=Eicar-Signature
eicar + 61 B tail (129 B)                  clean
eicar after one byte                       clean
```

HTTP 接纳链（`create_app` 指向真实 clamd，独立数据目录）：

```
readyz: {'database': 'ok', 'storage': 'ok', 'worker': 'stopped', 'malware_scanner': 'ok'}
clean upload: scanning {'status': 'pending'}
clean final: ready  scan={'status': 'clean', 'engine': 'ClamAV 1.5.4/28134/…', 'attempts': 1}  download 200
eicar final: rejected scan={'status': 'infected', 'signature': 'Eicar-Signature', 'engine': 'ClamAV 1.5.4/28134/…'}  download 410 rejected_malware  blob present: False
eicar again: 400 malware_detected
template clean: 200 scan={'status': 'clean', 'engine': 'ClamAV 1.5.4/28134/…'}
template eicar: 400 malware_detected {'signature': 'Eicar-Test-Signature'}
capabilities malware_scan: {'mode': 'required', 'configured': True, 'max_bytes': 26214400}
log: scan.rejected source=s_… tenant=- sha256=131f95c5… signature=Eicar-Signature engine=ClamAV 1.5.4/28134/…
```

测试替身随后按真实行为收紧（只在偏移 0 命中），避免替身放过真实引擎会判"干净"的样本。EICAR 命中证明检测链路可用，
不证明能拦截所有恶意文件；真实引擎的常驻内存与签名数是 compose 内存限额的依据。

### 16.21 风险触发的人机验证：服务端校验、绑定与故障策略（CI-21）

**接口测试**（`services/identity/tests/test_challenge.py`，9 项，siteverify 替身记录每次表单）：

```
services/identity/tests/test_challenge.py .........                       [100%]
```

覆盖：秘密与幂等键只发往 siteverify；缺失/畸形令牌不上网；跨 action、跨 hostname、已用（`timeout-or-duplicate`）、无效、过期
分别拒绝；网络错误用同一幂等键重试一次，再失败与 `invalid-input-secret`、5xx 都是「不可用」；`/api/auth/state` 有 site key 无 secret；
注册无令牌 400 且无账户无邮件、坏令牌 400、找回表单的令牌用于注册 400（`reason: action`）、好令牌 202 且一封邮件；不可达 503 +
`Retry-After: 30` 且无副作用；找回、验证码登录、重发同样被挡；口令登录首次不挑战、同地址 3 次失败后必须先带令牌；未配置时一切照旧。
`tests/integration/test_readyz_components.py` 另外证明组合服务器的 `/readyz` 报告 `malware_scanner`（首次部署时漏接的那一处）。

**浏览器（隔离实例，Cloudflare 公布的测试密钥；两引擎，390×844，三种语言）**：

```
== chromium (Google Chrome)
  - state exposes site key ending …00AA and actions ['forgot', 'login', 'login_email', 'register', 'resend']; no secret
  - zh-Hans: widget solved → token sent → Cloudflare siteverify accepted it (test key: hostname example.com, no action) → server refused on the action binding; message in the page's language
  - zh-Hans: 0 CSP violations, 0 page errors with the widget script and iframe loaded
  - zh-Hant / en: 同上
  - recovery and mailed-code forms show the widget; the password sign-in form does not
== webkit (Playwright build; *.localhost through the local proxy)
  - 同样八条通过
== 失败密钥 2x…AA（Chromium）
  - zh-Hans / zh-Hant / en: widget solved → token sent → Cloudflare siteverify rejected it → challenge_failed/invalid; form stays, message in the page's language
```

审计行（隔离库）证实服务端确实问过 Cloudflare：`{"action": "register", "reason": "hostname", "hostname": "example.com"}`，允许
`example.com` 后 `{"reason": "action", "token_action": null}`。**结论边界**：测试密钥的令牌不带 `action`，所以浏览器里的注册无法
用测试密钥走到 202；这不是缺陷，是绑定检查在起作用。完整通路要等生产 Widget（账户持有人创建）。运行后隔离实例已恢复为无密钥配置。

### 16.22 平台通知中心：真实事件、读者语言、邮件 outbox（CI-22）

**接口与调度器测试**（`services/identity/tests/test_notifications.py`，6 项）：

```
services/identity/tests/test_notifications.py ......                       [100%]
```

覆盖：同一行按读者语言渲染（英文/繁體）与服务端链接；他人不可读、不可标记；`dedupe_key` 去重；`read-all` 只到打开列表时的
`latest_seq`，之后到达的仍未读；偏好关掉类别则不落行，安全类别始终落行且投递 pending；偏好校验（未知类别、25:00、火星时区、
非法预览）；路由层：owner 批量邀请已有账户的成员 → 成员收到英文标题的待处理项 → 标记已读不接受邀请（仍在「待处理」）→
改口令产生 `critical` 行；调度器：两条投递恰好各寄一封、第二轮 0 封；汇总偏好把两条合成一封含两条正文的邮件；免打扰时段推迟
普通通知而安全通知照发；邮件未配置时投递 `skipped`（原因可见）而站内行照常。

**浏览器（隔离实例，Chromium 与 WebKit，390×844）**——先跑一遍 CI-19 的注册→邀请→接受闭环（真实产生 `invitation.received`
→ 接受后 `done`，邀请人得到 `invitation.accepted`），再跑 `ui_e2e_inbox.py`：

```
== chromium (Google Chrome)
  - bob.verify@example.test (zh-Hant) has 1 notifications, 1 unread
  - drawer lists the same first item the API renders for this reader: Newcomer 50f520 已加入「Loop Team d946」
  - marking one item read took the unread count from 1 to 0
  - and back to 1 after marking it unread
  - mark-all-read → 0 unread; the items stay listed
  - preferences section saves (organization e-mail immediate→digest→immediate); the security row is locked on
  - 0 CSP violations, 0 page errors
== webkit — 同样通过
```

铃铛在手机视口位于账户外壳的菜单抽屉内；徽标数字与 `/api/auth/notifications/unread-count` 一致；读者的繁體界面下标题为繁體，
成员名保持原文。跨源：同一账户会话经单点登录进入门户与 Synwevo Docs 后，两处顶栏的铃铛与未读数来自各自源的接口、同一张表：

```
  - portal bell present; portal origin reports 1 unread from the shared table
  - Synwevo Docs (after single sign-on) shows the same bell, count 1 and the same first item from its own origin
```

第一次跑时 Docs 与门户对 `/api/auth/notifications/*` 回 404 `not_in_this_role`——角色门只放行明确列出的账户路径；把五条收件箱路径
加入 `APP_ACCOUNT_PATHS` 后通过，并有 `tests/integration/test_readyz_components.py` 断言三种角色都以 401（无会话）而非 404 应答。

### 16.23 组织自定义角色：委派不越权、有效权限可见（CI-23）

**接口测试**（`services/identity/tests/test_org_roles.py`，5 项）：目录与基础集合一致（admin 默认集合不含 `org.roles.manage`，两项所有者专属不可委派）；
所有者建角色并分配后，成员恰好获得那两项（能建空间、能看成员，仍不能改设置/管角色）并收到 `organization.roles_assigned`；受委派的角色管理员
不能铸造未持有的权限（403 `permission_not_held`）、不能编辑或分配含更高权限的角色、能分配自己创建的角色；邀请权限经
`require_invitation_permission` 生效，「仅所有者」策略压过任何角色；删除已分配角色 409 `role_assigned` → `unassign` 后清除；同名 409、
未知权限 400、版本冲突 409；移除成员清空其角色；admin 基础角色照旧（仍不管角色）。组织路由、邀请、通知三套测试 33 项一起通过。

**浏览器（隔离实例，390×844，所有者为繁體读者、成员为简体读者）**：

```
== chromium (Google Chrome)
  - owner created role “Space builder 1a7a” with workspaces.create + members.view; owner-only boxes were disabled
  - assigned; the effective-permissions view lists the two permissions and names the role as their source
  - member holds exactly the two permissions, created a workspace (201), was refused a rename (403), and found the assignment in the inbox in English
  - deleting the assigned role said 1 member was affected and left the member with no organization permissions
  - 0 CSP violations, 0 page errors
```

过程中发现并修正：成员面板的角色列表在页面挂载时取一次，刚在角色面板创建的角色在「分配角色」对话框里不出现——改为打开对话框时重取；
敏感写操作的重认证提示是异步出现的，脚本要等待口令框而不是立刻判断。WebKit（经本地 `*.localhost` 代理）同一脚本五条全部通过。


### 16.24 机构邮箱注册（CUHK）：根因定位与在产品内能修的部分（CI-24）

**生产现场（2026-09-26 只读、脱敏查询；不含本地部分、不含正文）**：`link.cuhk.edu.hk` 与 `cuhk.edu.hk` 的 MX 都是
Trend Micro Email Security（`*.in.tmes.trendmicro.com`）。生产库里唯一一次 CUHK 注册（2026-09-25 16:09:20Z）：账户已建（`pending`）、
验证令牌已签（24 h）、outbox 行 `sent`（1 次尝试，16:09:37Z）；mailcow 队列 `E0C8450BA22` 由 rspamd 以 `hikosphere.com s=mailcow2026`
签 DKIM，16:09:45Z 中继到 `mail.smtp2go.com:587` 得到 `250 OK id=1xA8Ua-…`；令牌到期前从未被使用；同一来源 3 分钟后改用消费邮箱注册并在
1 分钟内完成验证。发件信箱在此后没有收到任何退信/DSN。DNS：`hikosphere.com` SPF 为 `ip4:149.28.18.222 include:_spf.mx.cloudflare.net ~all`
（不含 SMTP2GO 出口），DKIM `mailcow2026` 已发布且与 rspamd 持有的公钥一致（对齐），DMARC `p=none`。

**结论**：不是前端正则、不是服务端语法校验、不是注册策略、不是限流、不是发信失败——应用与中继链完整；最终裁决发生在 SMTP2GO 之后的
收件网关（延迟/隔离/垃圾箱，或拒收退给 SMTP2GO 的回信地址），只在 SMTP2GO 的 Activity 报表里可见（需要账户持有人查看，见
operations.md §6.1）。产品侧的真实缺口是：收件页不说明邮件来自谁、不提示机构网关会延迟/隔离、不说明重发规则；已有活跃账户的邮箱再注册时
服务端静默不发信，人会一直等一封不会来的邮件；校验器遇到尾随点域名会抛异常（500）。

**修改**：`valid_email` 不再抛异常（尾随点、空标签、连字符开头/结尾标签 → False → 400 `bad_email`）；邮件正文改为 quoted-printable
（rspamd `MIME_BASE64_TEXT` 这类信号不再出现）；`/api/auth/state` 公布 `mail_sender`（名称 + 地址，本就在每封邮件里）；`/verify-email`
无令牌态新增提示框（发件地址与搜索建议、机构网关延迟/垃圾箱/隔离区与允许名单、每小时最多 3 封且只有最新一封有效、链接 24 h、
「换一个邮箱注册」），三语言；对已有活跃账户的地址再注册，同形 202 之外向该信箱寄「账户已经存在」提示（登录/找回链接，无令牌，
每小时最多一封，审计 `auth.registration.existing_account`）。

**接口测试**：`services/identity/tests/test_email_addresses.py`（CUHK 学生/教职员/别名、多级子域、公司域名、加号标签共 10 种接受；
尾随点、无点、连字符边界、空标签、控制字符等 11 种拒绝且不抛异常；去空白；规范化只做 casefold，不合并点号/加号/不同子域）；
`test_registration_activation.py` 新增 3 项（尾随点 400 而非 500；CUHK 形式注册 → `state.mail_sender` → outbox `verify` → 落盘 .eml 为
quoted-printable 且含链接；已有活跃账户再注册 → 同形应答、仅一封 `account_exists`、无令牌、口令与状态不变、一小时内不重复）。
上述文件与邮件传输、身份、外壳资源对等测试合计 35 项通过。

**浏览器（隔离实例，文件邮件，390×844，Chromium + WebKit）**，`services/identity/scripts/ui_e2e_institutional_mail.py`：

```
== chromium (Google Chrome)
  - chromium: trailing-dot address → 400 bad_email (not a 500)
  - chromium: registered 1155…@link.cuhk.edu.hk on a 390px viewport; the inbox screen names the sender and the gateway/quarantine guidance in zh-Hans, en and zh-Hant
  - chromium: the verification mail is quoted-printable UTF-8 with a fragment-carried link (subject: 验证你的 Synwevo 邮箱)
  - chromium: the link only landed; confirming with a mailbox-owner password activated the account
  - chromium: signed in with the institutional address (status active), signed out
  - chromium: re-registering the now-active address showed the same screen and mailed an "account already exists" notice without any token
  - chromium: 0 CSP violations, 0 page errors
== webkit (Playwright build; *.localhost through http://127.0.0.1:52017)
  - （七条同上）
```

截图 `verify-email-chromium-zh-Hans.png`：白蓝提示框在 390 px 内完整可读，无横向滚动。

**未验证（需要外部条件）**：真实 CUHK 信箱的最终收件（隔离区/垃圾箱/收件箱）与 SMTP2GO 对 `1xA8Ua-…` 的投递记录；这两项只有
账户持有人能看。SPF 补 `include:spf.smtp2go.com`、在 SMTP2GO 验证发件域（对齐的回信地址与 DKIM）、关闭事务邮件的链接/打开跟踪、
把 From 改到 `wegreen.ltd`（mailcow 已为该域发布 `mailcow2026` DKIM）均为 DNS/供应商侧操作，步骤在 operations.md §6.1。

### 16.25 多格式成员批量导入：文件不是同意，角色不从名字来（CI-25）

**架构**：文档引擎的读取器由组装根 `synwevo_server` 作为 `DocumentIngest` 注入账户服务（与 `DocsControlPlane` 同一模式）；账户服务不导入
Docs（`check_boundaries.py` 通过，348 个 Python 文件）。clamd 适配器搬入 `synwevo_core.malware`，Docs 保留再导出；Tesseract 子进程改为最小环境。
schema v11 新增 `member_imports`/`member_import_rows`。决策记录、状态机、原因目录、格式与限制：`docs/platform/member-import.md`。

**接口测试**：`services/identity/tests/test_member_import.py`（3 项，标准库读取器）：JSON/Markdown/TXT 读取与映射建议；含表头的 CSV 计划出
邀请 2 / 更新 1 / 跳过 2（文件内重复、导入者本人）/ 阻断 3（坏地址、空间 owner、未映射的组织角色文字）；确认前无人成为成员；有阻断行时必须显式
「只导入有效行」；版本冲突 409；执行后邀请邮件入队、已有成员空间角色 viewer→editor 并获得自定义角色、导入者收 `member_import.finished`；
新人通过邮件链接接受后加入组织、获得导入指定角色、收 `member_import.roles_applied`；委派被撤销的导入者：计划时 `role_not_delegable`、执行时行失败且
不发邮件；无权限成员 403；按行模式、取消（剩余行 skipped）、XLSX 在无引擎实例上如实报 `needs_engine`。
`tests/integration/test_member_import_engine.py`（3 项，`synwevo_server` role=all）：XLSX（含隐藏工作表、前导零本地部分保留、映射建议）；
DOCX 表格 + 段落行、有文字层的 PDF 按页/行、JSON；PNG → `needs_ocr` → 按需 OCR（本机 Tesseract chi_sim+eng）识别出两行地址并带页码/行号/置信度。
本轮相关套件（导入 6 项 + 外壳资源对等）全部通过；组织页资源新增 73 条三语言字符串。

**浏览器（隔离实例，文件邮件，390×844，Chromium + WebKit）**，`services/identity/scripts/ui_e2e_member_import.py`——全部真实：新所有者经邮件链接注册、
建组织与自定义角色；上传由 openpyxl 生成的工作簿；引擎读取；表头映射自动建议、组织角色文字需手动映射；预览「邀请 1 / 跳过 2 / 阻断 1」；确认对话框
理由 + 重认证；后台执行完成、报告可下载；新人经邮件接受邀请后恰好持有该角色的两项权限并在收件箱看到通知：

```
== chromium (Google Chrome)
  - fresh owner registered through the mailed link, created an organization and a custom role
  - workbook read by the document engine; header mapping suggested; the role text needed an explicit mapping; preview: invite 1, skip 2, blocked 1
  - confirmed with a reason and re-authentication; the runner finished (invited 1); the CSV report downloads
  - the newcomer accepted the mailed invitation, is an organization member with exactly the imported role's permissions, and was told in the inbox
  - 0 CSP violations, 0 page errors
== webkit（五条同上）
```

截图 `import-read/preview/done-chromium.png`：390 px 内表格预览换行、筛选标签与逐行原因可读。

**未做/外部条件**：生产 `accounts` 容器要合并 compose 里新增的扫描器两项才会真正扫描导入文件（未合并前页面如实显示未配置）；
XLS/ODS/DOC 经 LibreOffice 的路径只在引擎单测覆盖，本轮未在导入链路单独跑；PDF 没有表格结构（按行）。

### 16.26 组织内私聊与群聊：显式成员、从加入起的历史、撤权即关门、流只是提示（CI-26）

**架构**：`synwevo_core.messaging`（schema v12：会话、带 `joined_seq` 的成员、按会话单调 `seq` 的消息、反应、附件），账户路由 `/api/auth/messaging/*`，
异步生成器实现的 SSE 流（每标签页一个队列与协程，25 s 心跳并复查会话/账户，会话撤销即 `end`），进程内扇出；客户端 `EventSource` 自动重连、
每次 `hello` 后按最后序号补取，连续失败或 `end` 后转 5 s 轮询并显示「轮询中」。附件走 `accept_upload`（同一 clamd 策略）+ 核心 blob（内容哈希），
下载只给能读到该消息的成员，图片内联带 `nosniff`/`sandbox`/`X-Content-SHA256`/`no-store`。组织移除（`remove_member`）一次关闭该组织全部会话。
决策记录、规则、接口：`docs/platform/messaging.md`。

**接口测试**（`services/identity/tests/test_messaging.py`，4 项）：目录只列同组织活跃成员、外人 404；私聊两侧打开是同一会话；`client_id` 重试不重复；
阅读位置与未读（对方 2 → 读到 1 → 读完）；私聊一次爆发只产生一项收件箱、发送者不收；反应固定集合、编辑/删除仅本人；第三人对会话 id 一律 404。
群：创建后成员收 `conversation.added`；后加入者只看到加入之后（`floor`>0）、搜索也搜不到之前；提及产生 `conversation.mention`、普通群消息默认不打扰；
成员不能管理；移除后 404/列表空/未读 0；再加入从新起点；群主须交接才能退出；归档后不能发；`Organizations.remove_member` 后所有会话消失。
附件：PNG 嗅探为 image/png、未用附件只对上传者可见、发送后一次性绑定、成员下载 200（内联/附件两种 disposition）、非成员 404、删除消息后 404、
超 20 MB 413。流：`hello` → 另一线程发布的事件到达 → 会话撤销后在下一次心跳发出 `end` 并结束，连接计数归零。

**浏览器（隔离实例，文件邮件，两个独立上下文：所有者 1280 px、成员 390 px，Chromium + WebKit）**，`services/identity/scripts/ui_e2e_messaging.py`：

```
== chromium (Google Chrome)
  - owner registered and created an organization; the member joined through the mailed invitation (phone-sized session)
  - the DM reached the member's list and badge over the stream; reply-with-quote and a reaction from the phone showed up live on the owner's desktop
  - an image attachment was scanned, stored once by content hash and rendered inline for the member (image/png, hash header, no-store)
  - group created, the member read it, was removed from the group info drawer, and from then on history is 404 and the group is gone from their list
  - search returned the owner's DM hit; the removed member's search of the group's text found nothing
  - 0 CSP violations, 0 page errors
== webkit（六条同上）
```

**未做/外部条件**：Docs 项目/任务链接卡片（按接收者权限生成摘要）未做，链接只做净化后的可点击文本；在线/他人已读不做（无可靠依据）；
多 worker 部署前需共享事件总线（compose 当前每角色单 worker）；Web Push 未做。
### 16.27 个人项目复制到组织工作空间：目标说了算，批准的是清单，复制的是快照（CI-27）

**架构**：`synwevo_core.project_copies`（schema v13：`project_copies`，状态机 pending→approved→running→done，旁路 rejected/withdrawn/expired/stale/failed，
`version` 乐观锁，待审批与批准后各 7 天时效），Docs `synwevo_docs.copies`（清单与摘要、执行器一次事务复制、10 s 运行器、自行批准即唤醒）与 5 个路由
（`/api/copy-targets`、`/copy-manifest`、`/copy-requests` 申请/列出、`/copy-requests/{id}/withdraw`），账户 `approval_routes.py`（组织审批列出/查看/决定，
决定 = `org.approvals.decide` + 目标空间 admin/owner + 近期重认证 + 理由）。清单只含名称/大小/哈希/数量/扫描状态；执行前重查来源方、目标可写、
决定者权限、组织活跃与摘要，任何一项变了都不复制（`stale`）。原项目不被写入；来源 blob 共享，其余对象在目标租户新建。
决策记录、规则、接口：`docs/platform/project-copy.md`。

**接口测试**（`tests/integration/test_project_copy.py`，2 项，`role=all` 组合服务器 + 真实引擎）：个人项目（工作簿 + DOCX 模板一个字段 + 逐行规则 + 映射）；
目标列表只有可写的组织空间、`can_self_approve=false`；清单 1 字段/1 映射/1 来源含哈希；外人目标为空；重复申请 409；只有可决定者收到 `copy.requested`；
无权限成员 403、外人 404；持权限的组织管理员但只是目标 viewer：能看、`can_decide=false`、决定 403；审批人在目标空间对源项目 404；未重认证 → `reauth_required`；
理由过短 → `reason_required`；批准记录决定者、申请人收到 `copy.approved`；二次决定 `not_pending`；运行器执行 → `done`，副本在目标空间：模板新建 v1 一个字段、
来源新 id 同 blob、`mapping_rev=1`、`origin=copy:*`；**副本真实生成 2 个 DOCX，产物读回含「张三」「李四」**；原项目 rev/mapping_rev/模板不变、无任务；
副本对个人空间 404。第二项：批准后改名摘要不变、改样本规则摘要变化 → 执行为 `stale: source_changed`，目标无副本、申请人收 `copy.failed`；撤回后决定 `not_pending`；
拒绝带理由到达申请人；目标空间 owner 的个人项目自行批准（`self_approved=true`）→ 执行 `done`；组织空间项目作为来源 409 `source_not_personal`。

**浏览器（隔离实例，文件邮件，两个独立上下文：审批人 390 px 触摸、申请人 1280 px，Chromium + WebKit）**，`services/identity/scripts/ui_e2e_project_copy.py`：

```
== chromium (Google Chrome)
  - owner created an organization; the requester joined its workspace as an editor through the mailed invitation
  - the requester asked from the project menu; the dialog showed the manifest (1 template field, 1 mapping, 1 workbook) and no self-approval offer
  - the owner approved at phone width with a reason; the step demanded a fresh password before it went through
  - the copy opened in the target workspace, previewed the first row, generated 2 documents; the downloaded ZIP read back both names
  - original unchanged (same revision, mapping revision, template, name) and invisible to the approver; the copy is unreachable from the personal workspace
== webkit（五条同上）
```

过程中修正的两处：审批条目原来只显示源项目名，现在显示副本名并注明「源项目「…」」；来源文件的扫描状态原来直接显示内部值（`not_configured`），
现在按目录翻译（等待检查 / 未配置扫描 / 检查失败 / 超出扫描大小 / 检出威胁）。WebKit 首次失败是脚本按 CSS 顺序填写而非按标签填写；改为 `get_by_label` 后两引擎一致。

**未做/外部条件**：反向或组织间复制、审批人裁剪清单、邮件一键批准（决定必须在会话内重认证）均不做；生产复制执行器随 Docs 角色单 worker，
多实例时 `claim_approved` 条件更新保证一次。
### 16.28 自有界面只写能力名：词表、守卫、例外表与产物元数据（CI-28）

**盘点**（源码 + 隔离实例三语言实跑，2026-09-26）：Docs 能力矩阵、生成对话框、预览徽标、OCR 按钮、关于页正文、编辑器提示、模板选择器与引擎错误/说明
文案写出 LibreOffice（81 处）、PyMuPDF/pdfium（22）、Tesseract（19）、xlrd；账户外壳把拦截网关按厂商命名；任务校验徽标提示写出 python-docx/openpyxl/PDFium/Pillow；
经排版引擎转换的 PDF 产物 `Producer`/`Creator` 是转换器的名字。词表、边界与不改的项：`docs/platform/component-names.md`。

**守卫** `scripts/check_component_names.py`：扫描前端消息目录与 `tr()/t()` 字面量、六个 Python 包的自然语言字面量（错误、说明、能力行、邮件），
排除注释/文档字符串/标识符/URL/测试；`licensing.py` 整文件是归属页；`STABLE` 逐条写理由（当前 1 条：能力矩阵的许可声明字段 + 其三语言）。
首次运行 151 处；改完 0 处。已接入 CI verify、CLAUDE.md、AGENTS.md。

**改动**：后端 13 个模块 40 余条文案；`messages.server.ts` 19 条键与三语言、`messages.ts` 10 条改写 + 9 条新能力名；8 个组件/页面；`jobview.ts` 校验 `engine`
从库名改为格式族 id 并显示能力名；`auth-client/api.ts` 网关文案只说角色、厂商线索进 `detail.server`；`pdf_engine.stamp_output_metadata` 在完整模式下把转换得到的
PDF 产物 `Producer`/`Creator` 写成 Synwevo Docs、保留模板自带的作者/标题/关键词（基础模式原样返回）。测试更新 4 处，新增 `test_output_metadata.py`（2 项）。

**接口/单元**：Docs 套件全部通过（含更新的 `test_pdfium_engine`、`test_image_raster`、`test_image_template`）；外壳测试通过（`api-i18n` 的网关断言改为角色文案 + `detail.server`）；
真实转换验证：python-docx 生成的 DOCX 经 `convert_output` → PDF 的 metadata 为 `creator/producer = Synwevo Docs`，`author` 保持模板值。

**浏览器（隔离实例，Chromium + WebKit）**，`services/identity/scripts/ui_e2e_component_names.py`：

```
== chromium (Google Chrome)
  - capability matrix, About page prose and home are free of component names in zh-Hans / zh-Hant / en; the licence table still attributes the PDF engine
  - project, preview, generate dialog and the finished job (three languages, tooltips included) name readers and engines by capability only
== webkit（两条同上）
```

**不改/做不到**：关于与许可页的组件表与 AGPL 通知；人机验证 iframe 内的供应商品牌（不在 Synwevo 控制范围，若要去除是「是否继续使用该供应商」的产品决定）；
PDF 引擎基础模式下的 PDF 产物元数据；Google/Microsoft 登录主体。
### 16.29 Docs 编辑安全与结果通知：设置修订、任务/拒绝通知、副本来源（CI-29）

**问题**（代码证据）：`PATCH /api/projects/{pid}` 对名称/规则/输出/模板选择没有任何版本检查，两个窗口后到者静默覆盖（映射与模板早有 409）；
通知目录里的 `docs.job_finished`/`docs.file_rejected` 从未发出（来源行不记录上传者）；副本页面看不出来源与批准人。决策记录：`docs/platform/docs-edit-safety.md`。

**实现**：schema v14（`projects.settings_rev`、`source_files.created_by`）；`update_project` 接受可选 `base_settings_rev` → 409 `stale_project`（带当前修订），
名称/规则/输出/模板变化推进修订，`step`/归档不推进；上传路由记录登录上传者；`JobService._notify_finished` 在终态事务内写收件箱（发起人为登录用户时，
每次运行一条，结果词按读者语言）；`_notify_rejected` 在拒绝路径写收件箱（上传者）；项目详情 `copied_from`；外壳：`isStale` 识别 `stale_project`、规则/改名/换模板/
输出保存带修订、副本来源行与审批链接、错误文案与 6 条三语言字符串。

**接口测试**：`test_project_settings_rev.py`（1 项：陈旧 409 + 当前修订、新鲜推进、`step` 不推进、非整数 400、不带令牌不检查、列表带修订）；
`tests/integration/test_docs_notifications.py`（2 项：真实任务 → 一条 `docs.job_finished`，链接 `…/p/{pid}/jobs/{jid}`、"2 succeeded, 0 failed"（读者语言 en）；
扫描替身 → `rejected`、一条 `docs.file_rejected` 链接项目页、同内容再传 400 `malware_detected` 且不重复通知、`add_source` 无上传者不通知）；
`test_project_copy.py` 增 `copied_from`（申请 id、源项目名、申请人、批准人、组织、含来源；无源空间 id；源项目为 null）。

**浏览器（隔离实例，Chromium + WebKit）**，`ui_e2e_docs_upgrades.py` 与 `ui_e2e_project_copy.py`：

```
== chromium (Google Chrome)
  - the second tab's stale rule save was refused and reloaded (revision unchanged); after the reload its save went through
  - the job's end reached the bell inside Docs (title, counts) and its link opened the job page
  - (copy proof) the copy's page shows 「由 Member 的个人项目「…」经审批复制而来 · 由 Owner 批准 · 查看审批记录」 with the link to ?approval=<id>
== webkit（同上）
```

过程中的一处修正：陈旧保存的对话框内文案原来说「请刷新后…」，而页面已经自动重载；改为与映射冲突一致的「已刷新为最新设置，请在此基础上重新操作」。

**未做**：实时协同/自动合并、评论与提及、其它 Docs 事件（模板/映射变更、产物过期）。
### 16.30 手机宽度复查：复制对话框、副本来源行、Docs 内的平台铃铛（CI-30）

CI-27/29 的浏览器证据里申请人一直在桌面宽度；这里把申请人放到 390 px 触摸视口（Chromium + WebKit），`services/identity/scripts/ui_e2e_phone_copy.py`：

```
== chromium (Google Chrome)
  - at 390 px the project menu, the copy dialog and its submit button fit without sideways scroll; the request shows as pending in the dialog
  - the copy's provenance line, the generate dialog, the job page and the bell item all fit at 390 px; the bell item opened the job page
== webkit（两条同上）
```

检查方式：每一步断言 `scrollWidth <= innerWidth`，对话框、提交按钮、来源行、生成对话框、铃铛条目的 `getBoundingClientRect` 落在视口内（抽屉在滑入动画结束后再量——
第一次量在动画中途得到 394 > 390 的假阳性，脚本改为等抽屉位置稳定）。截图人工复核发现一处真实的手机问题：收件箱抽屉里的筛选按钮（全部/未读/待处理/全部标为已读/偏好）
继承了导航抽屉「每项一行 44 px」的规则，五个按钮竖排占去约 250 px；`notifications.css` 给抽屉内的 `.notif-tools .btn` 改回紧凑工具条尺寸（32 px、自动宽度、可并排换行）。
`check_design.py` 通过；三外壳重建后复跑通过。
### 16.31 账户侧的 Docs 链接：通知链接指错主机的修正，消息里的按读者解析卡片（CI-31）

**缺陷（有证据）**：`resolve_target` 对 `docs` 目标一律用 `SYNWEVO_APP_URL`，而账户服务里这个变量是账户服务自己的地址（`infra/server/app.py` 注释早已说明，
`bootstrap_docs_app` 也因此只在 Docs 进程登记地址）。隔离实例账户角色发出的邮件里出现 5 处 `http://accounts.localhost:31770/p/p_…`；生产 compose 的
accounts 容器同样不知道 Docs 地址。影响：CI-27/29 的复制、任务、文件拒绝通知在邮件与账户外壳铃铛里的链接不可用（Docs 内的铃铛不受影响，
因为它由 Docs 进程回答）。修正：`Notifier.docs_base()` 从应用注册表读 Docs 启动地址（缓存 60 s），`resolve_target(target, docs_base=)` 优先用它，
只有 `app`/`all` 角色才回退到 `SYNWEVO_APP_URL`。测试 `test_notification_docs_links.py`：账户角色 + 账户地址的 `APP_URL` → 未登记时无链接、登记后
链接到 Docs 地址、账户目标仍走签发者、Docs 进程可回退。

**新能力（9.4 A 未完项）**：消息中的 Docs 项目/任务链接卡片，按读者权限解析（`docs_links.py` 读侧能力 + `/api/auth/messaging/link-card`）。
`test_messaging.py::test_docs_link_cards_follow_the_readers_own_access`：所有者看到团队项目、任务（部分失败 8/2）与自己的草稿；团队空间成员看到
团队项目、看不到草稿；组织内非空间成员与外人一律 404；非 Docs 链接、异主机同路径、不存在的项目、别的项目的任务均 404。

**浏览器（隔离实例，所有者 1280 px、成员 390 px 触摸，Chromium + WebKit）**，`ui_e2e_link_cards.py`：

```
== chromium (Google Chrome)
  - the owner's three links became cards — team project (with workspace), the finished job (outcome and counts), and their own personal draft
  - the member (phone) got cards for the team project and the job, and only the plain link for the owner's personal draft; nothing scrolls sideways
== webkit（两条同上）
```
### 16.32 浏览器推送：VAPID 密钥、订阅与队列、派发、账户页设备管理（CI-32）

**研究与取舍**：MDN Push API、WebKit 官方博文（iOS 16.4+ 须加主屏幕且 manifest standalone、权限须来自用户手势、无需 Apple 开发者账号）、RFC 8030/8188/8291/8292；
依赖：pywebpush 2.5.0 会把 `requests`+`aiohttp`（十余个传递包）带进镜像，改用它的两块基石 py-vapid 1.9.4（MPL-2.0）+ http-ece 1.2.1（MIT，都只依赖已有的
`cryptography`）+ 平台已有的 httpx；在 venv 里先装后卸验证过依赖树，`pip check` 与锁文件一致。决策记录：`docs/platform/web-push.md`。

**实现**：schema v15（`push_keys`/`push_subscriptions`/`push_queue`）；`synwevo_core.push`（首次使用生成并入库的 VAPID 密钥、订阅校验与合并、每读者 ≤ 10 台、
aes128gcm 加密 + VAPID JWT（≤ 12 h）+ TTL/Urgency、结果分类 sent/gone/retry/failed、410 与连续失败撤销）；`Notifier`：类别 `push` 开关（安全始终开）、
有活跃设备才入队、队列的 due/claim/finish/retry；派发器每趟增加推送（免打扰推迟非紧急、每设备一次、不可达退避）；`/api/auth/push/*` 五个接口；
账户服务提供 `/sw.js`（`Service-Worker-Allowed: /`）与 `/manifest.webmanifest`（standalone）；登出撤销该会话的设备；账户页：每类「浏览器推送」列 +
「浏览器推送」小节（状态说明、在此设备上开启、设备列表、发送测试、移除），三语言 28 条。

**接口测试**（`services/identity/tests/test_push.py`，4 项，本机推送服务替身）：公钥稳定；订阅返回不含端点/密钥、同端点合并、`this_device` 标记、坏订阅 400、
移除后 404；测试推送带 `Authorization: vapid t=…,k=…`、`Content-Encoding: aes128gcm`、`TTL: 60`、`Urgency: high`，密文不含明文；一条通知推到两台设备各一次，
答 410 的设备被撤销、`push_queue=sent`、第二趟不重复；`docs` 类关推送不入队而安全类不能关；免打扰时段普通项推迟、紧急项照发；推送服务关闭后队列回退
（attempts=1、not_before>0）且设备保留；登出后 `revoked_reason=signed_out`。

**浏览器（隔离实例，Chromium + WebKit）**，`ui_e2e_push.py`：

```
== chromium (Google Chrome)
  - /sw.js (Service-Worker-Allowed: /) and /manifest.webmanifest (standalone) are served; the push config offers a public key
  - the service worker registered but this headless browser has no push service; the page said so and kept in-app notifications
== webkit（两条同上）
```

截图复核：偏好表多出「推送」列（安全行锁定勾选），小节按钮与说明可见，无头 Chromium 的订阅失败以 toast 说明「此浏览器无法完成推送订阅：Registration failed - permission denied」。

**未验证/外部条件**：真实设备上的收到与点击（Chrome/Android、iOS 16.4+ 主屏幕 web app、macOS Safari）需要人工在生产上开启一次并按「发送测试」；
无头浏览器背后没有推送服务，无法在自动化里真实订阅。VAPID 密钥轮换未做。
### 16.33 项目评论与提及：锚点读取时判断、提及只到有权的人、待解决线程（CI-33）

**研究**：Notion 帮助中心（页面级/行内评论、`@` 实时搜索、对无权页面的提及不通知、解决/重开）、Linear 文档（线程回复、`…` 编辑删除、线程解决），均 2026-09-26 读取；
锚定内容变化后的处理二者未写明，按 9.8 自定为「读取时重新判断」。决策记录：`docs/platform/docs-comments.md`。

**实现**：schema v16 `project_comments`；`synwevo_docs.comments`（成员名单只含姓名、一级+回复、四种锚点写入校验/读取判断、提及只限空间活跃成员且作者不算、
参与者通知、作者/管理员/可编辑者的权限分工、限流 60/分钟）；7 个路由（viewer 可读写评论，逐评论判权）；通知目录 `docs.mention`（立即）/`docs.comment`（汇总）；
项目页「更多 → 评论…」抽屉（未解决在前、锚点定位按钮或「已失效」徽标、关于=整个项目/当前字段/当前映射/当前文件、`@` 候选、回复/编辑/删除/解决/重开、
`?comment=` 深链高亮），三语言 32 条。

**接口测试**（`tests/integration/test_project_comments.py`，1 项，组合服务器）：成员名单只有姓名；提及 viewer → `docs.mention`（标题、`?comment=` 链接），
非成员与作者本人被丢弃且无通知；viewer 回复 → 参与者 `docs.comment`，回复者不通知；外人 403（空间门）/404（他空间）；他人不能编辑、作者编辑不重复提及；
viewer 不能解决、编辑可解决、默认列表隐藏已解决线程与其回复、重复解决 409、重开；删字段后 `anchor.valid=false, reason=slot_gone` 且标签保留；
坏锚点/空正文/回复的回复 400；viewer 不能删他人评论、空间 owner 可删且回复随线程消失。

**浏览器（隔离实例，编辑者 1280 px、查看者 390 px 触摸，Chromium + WebKit）**，`ui_e2e_comments.py`：

```
== chromium (Google Chrome)
  - the editor commented on the selected field and mentioned the viewer from the @ list
  - the viewer's bell (phone) carried the mention; its link opened the project with the comment highlighted; the anchor located the field and the viewer replied
  - the editor was told of the reply; after deleting the field the comment's anchor reads 已失效 with no jump; resolving hid the thread until 显示已解决
== webkit（三条同上）
```

过程中的两处脚本修正（非产品缺陷）：手机宽度的账户页铃铛在导航抽屉里，抽屉在窄屏钩子稳定前会自动关闭，脚本改为等页面静止后再开；
隔离实例的账户进程未重启时，新增的通知种类在旧目录里渲染成空标题（部署时三容器同镜像，不会出现），重启后通过。

**未做**：文字范围级行内批注、富文本/附件/表情、评论的实时推送到已打开的项目页、跨项目待办视图。
### 16.34 群公告与举报处理：钉在顶部的一段文字、只快照被举报消息的审核、重认证下的删除或驳回（CI-34）

**研究**：Telegram FAQ「Groups and Channels」（钉住消息显示在聊天顶部、所有成员得到通知），Microsoft 支持「Report a concern in Microsoft Teams (free)」
（任何人可举报、选类别附说明；提交线程信息/时间/举报人/类别/说明，群聊连带最近十条），Google Workspace 管理员帮助「Take action on reported Chat content」
（持服务设置权限的管理员审核；删除消息/删除空间/解决并附评论；解决后状态不可改），均 2026-09-26 读取。两处官方页面都没写举报人是否被告知结果，本项目补上。
决策记录：`docs/platform/messaging.md` §4.2。

**实现**：schema v17（`conversations.announcement/announcement_by/announcement_at`，新表 `message_reports`）；`synwevo_core.messaging`：`set_announcement`
（管理者、≤1000 字、系统行、归档群 409）、`report`（成员、非本人、可见的文本消息、一人一条未处理、快照正文与附件名）、`reports_for_organization`、
`review_report`（removed 走与群内删除相同的路径并推 `message_updated`；dismissed 保留；同一消息其它未处理举报随之处理；重复 409）、`messaging_managers`
（持 `org.messaging.manage` 的活跃成员）；4 个路由（举报每人每天 20 条；列表/处理要求权限，处理另要求 `admin` 范围严格重认证）；通知目录 4 种；
账户外壳：线程顶部公告条（两行截断可展开）、系统行、群信息里的公告表单、别人消息上的「举报」与说明弹窗、组织页「举报处理」区
（待处理/全部、快照引用块、`?report=` 聚焦、OrganizationAction 原因+重认证）；三语言 41 条。
顺带修正两处既有缺陷：线程收到 `conversation` 事件时不重读历史，改名/加人/公告的系统行要等下次打开才出现（改为收到即重读）；
审批区与举报区带深链进入时若该项不在当前页签会自动切到「全部」，但此前会一直切回，读者点不回「待处理」（改为只切一次）。

**接口测试**（`test_messaging.py`，新增 1 项）：成员改公告 403、多余字段 400、保存后去空白、成员各得一条 `conversation.announcement`（英文标题正确）、
群主不通知、系统行 `event=announcement`、清除后不再通知；举报自己的消息 400、无效原因 400、举报 201 且快照/发送者/举报人/会话正确、重复 409、
群主得到 `conversation.reported`（可操作、链接到 `/organizations/{oid}?report=`）、发送者不知情；发送者事后编辑不改快照；成员读列表 403；
群主读到列表与原因表；处理无重认证 → `reauth_required`；无效决定 400；加入后看不到该消息的成员举报 404；删除后 `reviewed_by`/`decision_note` 正确、
重复处理 409、待处理清空、`status=all` 可见；消息对所有人 `deleted=true` 正文为空；举报人收到 `conversation.report_removed`（标题与说明正确）。

**浏览器（隔离实例，群主与发送者 1280 px、举报人 390 px 触摸，Chromium + WebKit）**，`ui_e2e_moderation.py`：

```
== chromium (Google Chrome)
  - the owner pinned an announcement from 群信息; it sits at the top of the thread and the thread shows the system line
  - on the phone the announcement is pinned above the messages without sideways scroll; the inbox carries one conversation.announcement item
  - Ben reported Ann's message from the phone with a reason and a note; the dialog said what the managers will see
  - the owner's bell led to the organization page with the report focused (snapshot, reason, note); removing it demanded a reason and a fresh reauth; the item moved to 全部 as 已删除消息
  - Ben's thread shows 消息已删除 in place of the message and his bell (phone) carries the outcome with the owner's note
== webkit（五条同上）
```

过程中发现并修正（产品缺陷，非脚本问题）：群主保存公告后自己的线程没有出现系统行——线程只处理 `message`/`message_updated` 事件；
以及处理完举报后点「待处理」页签被立刻切回「全部」。两处都在上面的实现里修好并重跑通过。

**未做**：多条公告/历史；举报附带前后文（有意不做）；举报的邮件/推送以外的渠道；对举报人的匿名化（Teams 合规版的做法）；平台级（跨组织）的举报视图。

### 16.35 组织共享模板：发布即冻结、使用即复制、维护有权属（CI-35）

**研究**：Microsoft Learn「Create an organization assets library」（组织模板库在 Office 新建页作为「你的组织」标签；必须是模板文件；读权限；≤30 库），
Google Workspace 管理员帮助「Turn custom Drive templates on or off for users」（开放/审核/限制三种提交模式，Docs Templates 权限审批，分类），均 2026-09-26 读取。
两者的模板都是「新建时复制一份」，模板更新不改变已创建文档——本项目沿用这一模型。决策记录：`docs/platform/org-templates.md`。

**实现**：schema v18（`organization_templates`、`organization_template_releases`）；`synwevo_docs.org_templates.OrgTemplates`（发布冻结 head 版本的文件/字段/元数据；
列表与详情按当前空间所属组织与组织成员资格；使用 = 在当前空间复制成普通模板并记 `info.origin`；更新发布只在源 head 更新时产生新一次发布；弃用/恢复；
维护者 = 源空间编辑者或组织 owner/admin）；6 个路由（读 `None`、写 `write`）+ 审计 5 种；`GET /api/templates` 项多出 `publication`；
Docs 首页模板库行的「发布到组织… / 更新组织发布 / 弃用共享… / 恢复共享」与徽标、「组织共享模板」区（组织空间可见）；项目「设置模板」对话框的
「从组织共享选择」页签；三语言 31 条。

**接口测试**（`tests/integration/test_org_templates.py`，1 项，组合服务器，两个组织、三个空间）：viewer 发布 403；个人空间发布 400 `no_organization`、列表 `organization=null`；
发布冻结 v2（1 字段）、说明去空白、`can_manage`/`source_ahead`；重复发布 409；模板列表带 `publication`；另一空间的成员可见但 `can_manage=false`；
另一组织的成员列表为空、详情/使用 404；viewer 可看不可复制、非维护者更新 403；复制得到本空间 head=1 的模板（字段、`info.origin`）并绑定项目、`use_count=1`；
源模板加字段后发布仍 v2、`source_ahead=true`、副本不变；显式更新 → 第 2 次/第 3 版/2 字段，未变 409，只改说明可行，发布历史 [2,1]，副本仍 1 字段；
弃用后默认隐藏、`include_deprecated` 可见含说明、使用 409 `deprecated`、副本无恙；组织 owner 恢复，重复 409；源模板删除后发布仍可用（`source_gone`）而更新 409。

**浏览器（隔离实例，群主 1280 px、成员 390 px 触摸，Chromium + WebKit）**，`ui_e2e_org_templates.py`：

```
== chromium (Google Chrome)
  - the owner published the library template to the organization from its row menu; the row shows 已共享 and the shared section lists release 1 (template v2) from Team A
  - on the phone the member chose 从组织共享选择 → 使用; the project got a copy of release 1 in Team B with the field 客户名称
  - the owner released version 2 after adding a field, then deprecated the publication; both tables say so
  - the member's copy still has one field and release 1; the deprecated publication is hidden from the picker and refuses new uses
== webkit（四条同上）
```

脚本修正一处（非产品缺陷）：手机上的项目页有两个「设置模板」（步骤列表与目标面板），脚本取面板的那个。

**未做**：发布审核队列与分类、发布通知、复制前预览、副本里「源发布有新版本」的提示、跨组织共享。

### 16.36 机器 API 与 SDK/CLI 的组织共享模板：委托令牌代表人，服务账户按名拒绝；覆盖清单重新审计（CI-36）

**问题**（代码证据）：CI-35 只在浏览器 API 提供组织共享模板；9.8「API 自动化一致」要求新能力进入真实契约。另外 `operation-coverage.json`
自上次审计后已过期：OpenAPI 里 14 个来源文件操作（结构/视图/块/搜索/词/内容/页图/选项/修正/代表文件/定位器/排序/定位/记录）在审计表里没有条目，
工具直接报 `unreviewed_routes`。

**实现**：`/api/v1` 新增 6 个操作（列表分页、详情含发布历史、发布（幂等）、更新发布、弃用/恢复、使用（幂等，可绑定项目）），全部要求
**委托令牌**——组织成员身份属于人，服务账户/API 密钥答 403 `delegated_required`；OpenAPI 重新导出（73 路径）；Python SDK 7 个方法、CLI
`org-templates list|get|publish|release|deprecate|restore|use`；审计表补上 14 条来源操作（12 条有 SDK 方法，页图与定位器探针没有）；
`docs/integration/api.md` §3 与 `docs/platform/org-templates.md` §2 记录机器契约。

**验证**：`tests/integration/test_org_templates_v1.py`（真实授权码流程取委托令牌、client_credentials 取服务令牌：发布幂等重放同一发布、重复 409；
服务账户读写皆 `delegated_required`；另一空间成员分页列表、详情发布历史、非维护者更新 403、使用幂等并绑定项目、副本只在本空间可见（他空间 404）；
个人空间 `organization=null`；无新版本 409 `unchanged`、机器 API 加字段后更新 → 第 2 次发布；弃用后隐藏/`include_deprecated`/使用 409；恢复；副本仍第 1 版）；
`test_gate_roles.py`、`test_api_docs.py`（OpenAPI 与代码一致、每个机器路由都有文档）通过；SDK 单元 20 项通过；
**SDK 验收**（`python_full_flow.py`，临时隔离服务，服务账户）：`{"result": "passed", "clients": ["sdk", "cli"], "documents_verified": 24, "builtin_outputs_verified": 52}`，
以其 `app-http.jsonl` 重新生成覆盖清单：`{"operations": 88, "sdk_implemented": 84, "cli_reachable": 62, "real_service_success_observed": 40}`——共享模板 6 个操作需要委托令牌，验收流程用服务账户，因此它们的「真实观察」为 0，
以集成测试为证据，README 已写明。

**顺带补证 CI-35 的核心主张**（`test_org_templates.py` 第 2 项，作业线程真实运行）：从发布复制出的模板与普通模板一样生成——DOCX 副本在 Team B 用
Team B 自己的来源行生成 2 份并读回「张三」「李四」；PNG 图片模板（单号框 + 明细重复区）发布→复制→按订单分组生成，全部样本成功，产物是与模板同尺寸
（1190×900）的 PNG。

**未做**：验收流程的委托令牌分支；CLI 对来源文件 14 个操作的命令（SDK 方法已有）。

### 16.37 取值语义端到端核实：显示值进文档、前导零与长编号、大整数、空行不是样本（CI-37）

**方法**（第六节 6.2：固定样本、先核实旧实现）：一个 XLSX（文本 `0007`、20 位文本编号、16 位整数、超过 2^53 的整数、`0.1+0.2`、`0.0%`/`0%`、`yyyy-mm-dd`/`yyyy/m/d`、
空单元格、`000000` 补零格式，中间夹一整行空行）+ 一个 DOCX 模板八个字段，按行生成并读回段落；再一个 CSV（BOM，前导零、20 位数字、`1e5`、`1.10`）同法。
期望值来自 `docs/FORMATS.md`（XLSX「数字格式显示值」；CSV「全部按文本保留」）。

**结果**：全部符合——`编号：0007`、`长号：12345678901234567890`、`大数：1234567890123456`（不写成科学计数）、`大数：9007199254740993`（逐位保留）、
`金额：0.3`（无二进制噪声）、`比例：12.5%`/`100%`、`日期：2026-09-26`/`2026/1/5`、空单元格为空而非 `None`、`补零：000007`；空行不计入样本（records total=2、产物 2 份）。
CSV：`0007`、`00000000000000000001`、`1e5`、`1.10`、`2.50` 原样。测试：`apps/docs/tests/test_value_semantics_e2e.py`（2 项）。

**过程中的发现**：实验最初「超过 2^53 的整数」失败，追到根因在**夹具生成器**——openpyxl 写入时把整数经 double 序列化（sheet XML 里已是 …992），
引擎的读取（openpyxl `_cast_number` → int）与 `Decimal(str(int))` 格式化都是逐位精确的；夹具改为直接改写 sheet XML 后通过。顺带加固 `xlsx_cache.py`：
把排版引擎算出的公式结果写回缓存时，整数直接 `str(int)` 而不再经 `float()`（此前所有数值都经 double 一次）。相关公式/引擎套件 6 项通过。

**XLSX 目标（`test_value_semantics_xlsx_target.py`）——发现并修复一个真实缺陷**：同一来源写入 XLSX 模板的六个单元格（常规、`yyyy-mm-dd`、`0.0%`、`#,##0.00`）。
第一次运行任务**失败**：`export_failed: 输出文件中找不到已填写的值：比例=100%`。根因在 `jobs.verify_output`：产物重开后按**来源显示文本**查找已填值，
而 XLSX 目标写入的是**数值**，显示由目标单元格自己的数字格式决定——来源 `1` 在 `0%` 下显示 `100%`，目标 `0.0%` 下是 `100.0%`，于是每一个来源与目标
格式不同的百分比、日期（`2026/1/5` 对 openpyxl 的 `2026-01-05 00:00:00`）都会被判成「没写进去」，整份样本失败（此前只对纯数字串跳过了这一查找）。
修正：XLSX 目标按值类型核对——数值类型不按显示文本查找（重开时以数值核对），日期/时间按 ISO 日期查找，其它按文本。修正后读回：文本保留前导零、
数字仍是 `int`/`float`、日期是真实日期且保留目标格式、`0.1+0.2` 写成 `0.3`、来源补零显示写入常规单元格时给出 B5 警告（文档承诺的行为）。
相关任务/验收/填充/预检套件通过。

**XLS 读取路径**（同文件第 3 项，需 LibreOffice 生成夹具，缺则跳过）：把同一 XLSX 转成 XLS 后经 xlrd 读取，逐格与 XLSX 读取结果的类型/值/显示文本一致；
唯一差异是超过 2^53 的整数——XLS 格式本身只存 double，文件里已是 …992，断言写明这是格式而非读取器。

**PPTX 目标**（`test_value_semantics_pptx_target.py`）：同一来源写入幻灯片文本框（按 slide/shape/para 建字段），读回整段文本逐字相等——
`0007`、20 位编号、`12.5%`/`100%`、`2026-09-26`/`2026/1/5`、`000007`。

**未做**：DOCX 表格单元格/页眉中的同类核实（既有验收 `test_acceptance_5_8::test_s7` 覆盖页眉与编号，未合并进本套件）。

### 16.38 新增界面的手机/平板回归审计：举报处理区、组织共享模板、评论（CI-33…36 的界面）

隔离实例上按 `ui_mobile_audit.py` 的规则（WCAG 2.2 1.4.10 回流、1.4.3 对比度、2.5.8 目标尺寸，另查未翻译文案）用一个种子账户
（组织 + 已发布的共享模板 + 项目评论；Docs 当前空间为组织空间）在 320 / 375 / 768 px 各量 15 页：登录/注册/找回/邀请/门户首页/404、
账户页、组织列表与组织页（含「审批」「举报处理」「组织共享模板」区）、两个成员页、门户工作台、Docs 首页（含「组织共享模板」表）与项目页。
结果：`0 page(s) with reflow, target-size, contrast or translation findings`（三种宽度共 45 页全部 ✓）。未量：评论抽屉与「设置模板」对话框的
「从组织共享选择」页签（对话框内容不在页面量测范围；CI-33/35 的手机证明里分别断言过无横向滚动）。

### 16.39 SDK 验收的「人」分支：真实登录 + 授权码（PKCE）取委托令牌，组织共享模板在真实服务上跑通（CI-38）

**问题**：CI-36 的覆盖清单里共享模板 6 个操作「真实观察」为 0——验收流程只有服务账户，而这些操作要求委托令牌。

**实现**：`tests/integration/sdk_acceptance/python_service.py` 的引导多出一个人（口令写入 0600 文件）、一个两空间的组织、一个公共客户端（登记回调地址，
从不真正访问，授权码从 Location 读取）；`python_full_flow.py` 把这些坐标交给 worker，并把证据断言拆成两条链：Cookie 只允许出现在 `/api/auth/login` 与
`/oidc/authorize`，令牌请求 = 服务账户的 client_credentials + 人的 authorization_code，业务请求的主体只能是 service_account 或 delegated，
且 6 个共享模板操作都必须以委托主体成功出现；`python_worker.py` 的 `person_flow`：A 空间上传并发布（幂等键重放同一发布——同键不同正文被 409
`idempotency_conflict` 拒绝，是契约不是缺陷）、B 空间 SDK 与 CLI 列表/详情/使用（幂等）并绑定项目、A 加字段后更新发布、弃用（B 列表为空、
`--include-deprecated` 可见、使用 409 `deprecated`）、恢复。

**结果**：`{"result": "passed", "clients": ["sdk", "cli", "person"], "documents_verified": 24, "builtin_outputs_verified": 52}`；
人的令牌请求 2 次、委托业务请求 22 次、Cookie 只出现在 ['/api/auth/login', '/oidc/authorize']；覆盖清单重生成：
`{"operations": 88, "sdk_implemented": 84, "cli_reachable": 62, "real_service_success_observed": 48}`（共享模板 6 个操作全部有真实观察）。SDK 单元 20 项与集成测试不变。

**未做**：CLI 对来源文件 14 个操作的命令；验收流程里 viewer 角色的委托令牌（写操作应被 403）——集成测试已覆盖。

### 16.40 预检问题带修复路径：从「生成」对话框一步到达它点名的字段或映射（CI-39）

**问题**（代码证据 + 9.8「预检与修复：显示受影响样本/页/区域及具体修复路径」）：`GenerateDialog` 把预检问题渲染成纯文本（`issueText`），
而服务端的每条问题早已带着对象 id（`slot` / `mapping` / `mappings`，见 `engine/plan.py`），界面没有用它——读者看到「必填未映射」「缺少列」后
要自己关掉对话框、切到字段或映射面板、再找到那一项。依据：NN/g 十项启发式 #9「错误信息……精确指出问题并建设性地提出解决办法」（2026-09-26 读取）；
项目自己的评论锚点定位（CI-33 `locateComment`）已经是同一种交互。

**实现**：`GenerateDialog` 导出 `fixTargetOf(issue)`（只认服务端给的 id，不从文案猜），静态问题与每个样本的首条问题旁多出「定位到字段 / 定位到映射 /
定位到文件 ↗」，按下即关闭对话框并调用 `onLocate`；`Workspace` 把它接到既有的 `locateComment`（字段 → 字段面板并选中；映射 → 映射列表并激活；
文件 → 切换文件；手机上切到对应面板）。三语言 3 条。

**浏览器（隔离实例，桌面 1280 px 与手机 390 px，Chromium）**，`ui_e2e_preflight_fix.py`：

```
== chromium (Google Chrome)
  - the generate dialog listed 必填未映射 for 问候 with 定位到字段; pressing it closed the dialog and selected that field
  - a mapping naming a missing column showed 缺少列 with 定位到映射; pressing it closed the dialog and made that mapping the active one
  - a rule naming a missing sheet showed 缺少工作表 with 定位到文件; pressing it closed the dialog and made that file the active one
  - on the phone the same button closed the dialog and the 目标 pane tab shows a selection (●)
  - the job page's skipped sample linked 定位到字段 into the project, which opened with that field selected and the locate parameter dropped
```

WebKit：五条同上（第一次 WebKit 运行只在最后的「无页面错误」断言失败——脚本在 `sign_in` 前多做了一次 `goto`，被取消的会话探测在 WebKit 里报成
"access control checks" 的 pageerror；去掉多余导航后通过，产品无 CORS 问题）。选中状态的断言走评论抽屉的「关于」候选（它按当前选中的字段/映射生成），不依赖内部 class。

文件级错误（`file_errors`，服务端带 `file_id`：工作表不存在、不是表格等）同样得到「定位到文件」，按下即切到该文件（手机上切到来源面板）。

**任务页**：失败/跳过样本的每条问题同样带「定位到字段/映射/文件 ↗」，是指向项目页的链接 `/p/{pid}?locate=<kind>:<id>`；项目页在首次加载后应用它并从地址里
去掉参数。字段选择要等模板加载完成再应用——加载模板的副作用会清空右侧选择（`tpl?.id` 的 effect），第一次实现时选择被这一步抹掉，
改为挂起到模板到位后再选。证明第 5 条：`error_policy=skip` 的批次把未映射必填字段的样本留成「已跳过」，任务页链接进入项目并选中该字段。

**未做**：任务页问题引用的对象在任务之后被删除时，链接只会落到项目页（无该对象可选）；没有提示「对象已不存在」。

### 16.41 评论抽屉静默刷新；深链指向已删除对象时明说（CI-40）

**问题**：CI-33 记为未做的「评论的实时推送到已打开的项目页」——抽屉打开后别人的新评论要重开才看到；CI-39 的 `?locate=` 深链在对象已删除时
什么也不发生（16.40 未做项）。

**实现**：`CommentsDrawer` 在打开期间每 30 s 静默重读（仅在标签页可见时），标签页重新可见时立即重读；重读只替换列表与成员，不碰正在输入的
正文与提及。`Workspace` 应用 `?locate=` 前核对对象存在（映射查项目映射表、文件查来源列表、字段等模板到位后查字段表），不存在则提示
「链接指向的字段/映射/文件已不存在」（三语言 1 条 + 复用既有的字段/映射/文件三词），不做任何选择。

**浏览器**（`ui_e2e_preflight_fix.py` 第 6 条，Chromium + WebKit）：`?locate=slot:s_nope` 与 `?locate=mapping:m_nope` 分别显示对应提示，地址参数被去掉。
静默刷新没有浏览器断言（30 s 定时 + 可见性事件，纯客户端），以代码审阅与 Docs 外壳单元测试（45 项）为准。

### 16.42 模板版本历史与恢复：恢复即新版本，恢复前说明影响（CI-41）

**问题**（9.8「项目与模板版本：版本记录、恢复前影响预览」）：模板详情早就返回 `versions`，但界面只有撤销/重做两个按钮——看不到版本列表，
回到更早的版本只能连按撤销，且撤销会被下一次编辑截断（重做链丢失）；也没有任何地方说明恢复会影响哪些项目。

**研究**（2026-09-26 读取）：Microsoft 支持「Restore a previous version of a file stored in OneDrive」——被选版本成为当前版本，原当前版本变成历史里的
上一版（历史保留）；Google 文档帮助「See what's changed in a file」——需要编辑权限才能浏览版本，「Restore this version」使之成为当前文档。
本项目沿用「恢复 = 新版本」：`restore_version` 以目标版本的文件与字段插入 head+1（parent = 当前 head），历史不删、撤销链保持线性；
`template_usage` 给出跟随 head 与固定版本的项目数，作为恢复前的影响说明。

**实现**：`GET /api/templates/{tid}/usage`（可读）、`POST /api/templates/{tid}/restore {version, base_head}`（写；`stale_template` 409、当前版本 400、
不存在 404）；项目页目标面板「版本」按钮与命令面板「模板版本历史…」→ 对话框：版本表（当前标记、说明、时间）、影响说明、逐行「恢复此版本 → 确认恢复」
并写明「将以第 N 版的内容新建第 K 版」；三语言 11 条（复用 2 条）。

**验证**：`apps/docs/tests/test_template_restore.py`（用量计数跟随/固定；旧 base 409；当前版本 400；坏版本号 400/404；恢复 v2 → v4 含 v2 字段、
说明「恢复自 v2」、历史 [1,2,3,4]、可撤销回 v3；固定版本的项目不变）；`test_gate_roles.py` 通过；浏览器 `ui_e2e_template_versions.py`
（Chromium + WebKit，桌面 + 手机）：

```
== chromium (Google Chrome)
  - 版本 listed v1–v3 with v3 marked current and one following project; restoring v2 made v4 the current version with only 客户名称, and v3 stayed in the history
  - undo after the restore returned to v3 with both fields
  - on the phone the 目标 pane's 版本 button opened the same list (four versions) without sideways scroll
```

**未做**：版本间字段差异的并排对比；机器 API 的 restore/usage（浏览器 API 先行，v1 契约待下一批）。

### 16.43 机器 API 的模板用量与版本恢复（CI-42）

**问题**：CI-41 只在浏览器 API 提供 `usage`/`restore`；9.8「API 自动化一致」要求新版本能力进入真实契约。

**实现**：`/api/v1/templates/{tid}/usage`（read）与 `/api/v1/templates/{tid}/restore`（write，`{version, base_head?}`，答案与 `GET /templates/{tid}` 同形），
OpenAPI 重导出（75 路径），API 台契约表补两条三语言摘要；SDK `get_template_usage` / `restore_template_version`，CLI `templates usage|restore`；
审计表补两行；验收 worker 的服务账户段多出用量 + 旧 base 409 + 恢复到 v1（历史 [1,2,3]）三步，以其证据重生成覆盖清单。

**验证**：`tests/integration/test_template_restore_v1.py`（服务令牌：用量计数、旧 base `stale_template`、非整数 400、恢复 v2 → head 4 含 v2 字段、
历史 [1,2,3,4]、详情一致）；`test_api_docs.py`、`test_api_platform_contract_i18n.py`、`test_gate_roles.py` 通过；SDK 单元 20 项通过；
SDK 验收（临时隔离服务，服务账户 + 人）通过：`{"result": "passed", "clients": ["sdk", "cli", "person"], "documents_verified": 24, "builtin_outputs_verified": 52}`；覆盖清单重生成：`{"operations": 90, "sdk_implemented": 86, "cli_reachable": 64, "real_service_success_observed": 50}`（用量与恢复两个操作有真实观察）。

### 16.44 正式 Logo 上线：三种确认资产、三个外壳、图标与缓存（CI-43）

**来源与分工**：品牌接入代码由并行会话「顶尖 README 文档」完成并留在工作树（`packages/ui/src/brand.tsx`、`brand-name.ts`、`brand/`、`public-brand/`、
`vite/brand-icons.ts`、`scripts/brand_assets.py`、三外壳 `index.html`/`vite.config.ts`、`PageState`、页脚字标等，见 `docs/design.md` §2.5 与
`docs/quality/progress.md` 顶部）；本会话按第 9.14 节复核、补一处手机品牌位、验证并作为一批提交发布（已通过跨会话消息与其确认文件清单）。

**资产**：`packages/ui/brand/originals/` 三张确认原图（完整版 1942×809、S 图形 1254×1254、字标 webp）；产品里的图形与字标从完整版像素裁出
（`brand_assets.py`：裁切、按比例重采样、图标底板、白色版，不重画不重排），`--check` 校验 20 个衍生文件与原图一致；favicon/触屏图标为 PNG，
由各外壳 Vite 指纹化（一年缓存、改图换名），`/favicon.ico` 由 `publicDir` 提供并以 `no-cache` 回答。

**复核（隔离实例，Chromium 1280 / 390 px，登录态）**：三个源各链接 3 个图标（全部 200 `image/png`）、`/favicon.ico` 200；顶栏 Logo `<img>` 真实加载
（naturalWidth>0）：账户页完整版 107×24、门户完整版 142×32 + 页脚字标、Docs 完整版 107×24；手机：账户与 Docs 顶栏为 S 图形（Docs 首页原为完整版，
本批加 `markBelow={720}` 与账户顶栏一致），门户登录页保留完整版（品牌位有空间，符合 9.14 表）。`test_shells.py` 新增 5 例（各角色的图标链接指向自己的
指纹目录、无 data: 占位、`favicon.ico` 类型与缓存头）；`brand_assets.py --check`、`@synwevo/ui` 19 项、auth-client 31 项、设计守卫通过；
强制颜色模式下用同图 mask 以 CanvasText 上色（Windows 高对比）。

**邮件里的 Logo**：现有邮件是纯文本，没有品牌图；随 9.15 的邮件设计系统一并交付（公共品牌资源 HTTPS 可取，见 CI-44）。

**生产核实（run 36212722189 部署 30a5cfd 后，2026-09-26 03:10 UTC，匿名只读）**：三个源 `/favicon.ico` 均 200 `image/vnd.microsoft.icon`（5336 B，`no-cache`）；三个外壳 HTML 引用带指纹的 icon-16 / icon-32 / apple-touch-icon PNG，全部 200 `image/png`（Docs `/assets/`、门户 `/portal-assets/`、账户 `/account-assets/`）。登录态顶栏与页脚只在隔离实例上截图核对（生产未用真实账户登录）。

### 16.45 系统邮件设计系统：一个布局、文本与 HTML 同源、三语言、带 Logo（CI-44）

决策记录与邮件清单：`docs/platform/mail-design.md`。

**实现**：`mail_layout.py`（`Mail` + `render` → 文本与 HTML）；`mail.py` 八类邮件重写为三元组（主题、文本、HTML；账户注销申请邮件从 `account_routes.py` 的两段纯文本并入目录），`enqueue(html=)`、`multipart/alternative`
投递、存档时 HTML 同样脱敏；`notify.py` 单条/汇总通知邮件同一布局；13 处调用点改为传 HTML；`/brand/email-logo.png` 公共品牌路由（一天缓存）；
schema v19 `mail_outbox.html`。

**验证**：`test_mail_catalogue.py` 5 项、`test_mail_transport.py`（multipart，文本与 HTML 同链接）、验证码登录/地址/通知套件通过；三套件全跑第一轮暴露 6 个仍按二元组解包或直接 `get_payload` 读多部件正文的旧测试（`test_invites.py`、`test_admin_governance.py`），改为三元组与 `get_body(plain)` 后通过，并顺带把失败投递的 HTML 脱敏也纳入断言；`mail_preview.py` 生成
30 份预览（8 类 + 通知单条 + 汇总 × 3 语言），Chromium 720/390 px 无横向滚动；以隔离实例为公共源渲染时 Logo 图片真实加载（`/brand/email-logo.png`
200 `image/png`）；隔离实例上真实注册（zh-Hant）产生的 `.eml`：`multipart/alternative`，文本部分首行为主题、含 `#token=` 链接，HTML 部分含同一链接与 Logo。

**未做**：真实邮箱客户端（Gmail/Outlook/Apple Mail）渲染与真实收件——需要授权的测试收件箱；邮件 Logo 纳入 `brand_assets.py` 清单。

### 16.46 Docs 实时多人协同：文档型模板的共同编辑、在场、归属与草稿（CI-45）

决策记录、接口与边界：`docs/platform/docs-collaboration.md`。

**实现**：服务端 `apps/docs/synwevo_docs/collab.py`（每模板一个房间：pycrdt 共享文档 + Yjs 同步/awareness 协议 + 自有元数据帧；握手复用平台会话门与
`Origin` 同源检查；查看者只读、写入丢弃并告知；每 30 s 以 `touch=False` 复核会话/账户/成员/应用资格——连接不延长会话；awareness 只接受连接证明的身份；
整体状态持久化到 `collab_docs`，逐块归属到 `collab_changes`）、`collab_pm.py`（ProseMirror JSON ⇄ 共享 XML，按 y-prosemirror 规则；索引单位为库的
UTF-8 字节）；模板服务在保存/恢复/撤销/重做/删除后通知房间（协作者的保存 = 快照；房间外的保存/恢复 = 替换实时文档）；schema v20；路由策略两条；
`WS /api/collab/templates/{tid}`、`GET /api/templates/{tid}/collab/changes`；核心包暴露 `api.state.session_gate`；新依赖 `pycrdt==0.14.6` 与 uvicorn
所需的 `websockets==15.0.1`（纯 `uvicorn` 不带 WebSocket 实现——隔离实例首次连接 404 暴露了这一点，生产镜像同样缺少，本批次一并修）。浏览器端
`apps/docs/web/src/lib/collab.ts`（y-websocket provider + 元数据帧 + 状态）、`DocEditor.tsx`（Collaboration/CollaborationCaret，关闭自带撤销栈；在线
成员条、命名光标、只读/断线/单人回退提示、变更记录抽屉；房间连不上或被拒即回退单人编辑已保存版本），共享令牌 `--peer-1…8`，48 条三语言字符串；
`infra/production/nginx.conf` 为 `/api/collab/` 加 Upgrade 头；Vite 代理 `ws: true`。

**验证**：`test_collab_pm.py` 4 项、`test_collab.py` 8 项（两编辑者收敛与名单、冒名 awareness 丢弃、查看者只读且被告知、无会话 4401/外站 4403/无 Origin
4403/他空间 4404/他人空间参数 4403/非文档模板 4400、离开后持久化与再进入、保存→v2 且房间已保存、恢复→所有人替换、房间外保存→重播种、逐块归属与
合并、在线降级与移出即时生效、撤销会话即断开且会话行不变）；typecheck/build/`npm test` 通过；五个守卫通过。
浏览器 `ui_e2e_collab.py`（隔离实例，两个真实账户在同一组织空间）：Chromium（Chrome，桌面 owner + 手机 390 px editor）8 个里程碑全部通过——
房间投递播种文档；**房间读出的文档保存为 v3 与单人路径保存的 v2 逐字节相同**（正文/页眉/页脚）；双方顶栏互见；任一方输入即时出现在对方编辑器并带
对方姓名的光标；变更记录按人按段落归属并带摘录（连续输入合并为一条计数）；owner 保存为 v4 且 editor 收到提示，editor 随后保存为 v5 无版本冲突对话框；
owner 把 editor 降为查看者后，editor 的连接在复核间隔内转为只读（角标「只读」、提示、保存禁用）；socket 永不应答时页面在等待后明确说明并单人
编辑已保存的 v5。截图 `chromium-01…05`。无 CSP 违规（`connect-src 'self'` 覆盖同源 `ws:`）。**WebKit** 同一脚本 8 个里程碑全部通过（截图 `webkit-01…05`；无 CSP 违规）——前提是测试用的本地代理助手 `localhost_proxy.py` 学会两件事：WebKit 把 WebSocket 握手作为绝对 `ws://` 请求行发给代理，且握手后要双向直通字节（此前答 400，页面走 10 s 超时回退）。三套件全跑：2098 通过、0 失败（`pytest-ci45.log`）。

**发现并修复的真实缺陷**：(1) 纯 `uvicorn` 没有 WebSocket 实现，升级请求答 404（生产镜像同样）；(2) CI-44 之后共享的浏览器证明助手
`link_in` 仍按单部件读取邮件正文，所有读邮件的 UI 证明都会失败——改为读取 `text/plain` 部件（`mail_text`），三个脚本同改；(3) 查看者的空 step-2
应答被当作写入而「告知」一次，之后真正的写入不再被告知——空更新现在静默忽略。

**未做**：表格型模板与项目页（字段/映射）的实时协同；逐字符差异视图；多进程房间共享；生产 nginx 副本的 Upgrade 头需运维同步
（见 deployment.md）。

### 16.47 Docs 项目页协同：在场、按映射合并、实时变更与归属活动（CI-46）

决策记录与接口：`docs/platform/docs-project-collab.md`。

**实现**：核心包 `context.actor()`（会话用户或委托令牌背后的人，由请求中间件设置，只用于归属）与 schema v21 `project_activity`；`services.py` 的
`merge_mappings`（按映射 id 三方合并，冲突与 `force_ids`）、`mapping_diff`、`save_mappings` 在 `base_rev` 落后时合并（无 id 的整份列表与空列表仍按旧规则
409；内容与已保存完全一致时不产生空修订）、`on_project_change` 钩子（项目设置、文件增删/顺序/代表/选项、映射保存/撤销/重做）；`collab.py` 项目房间
（在场 + 变更帧，不接受文档帧）、`on_project_change`（记录活动 + 推帧）、模板版本变化按使用项目扇出、`GET /api/projects/{pid}/activity`；`WS /api/collab/projects/{pid}`；
`PUT …/mappings` 的 `force_ids`。浏览器端 `ProjectPresence.tsx`（在场条、点击定位到对方所在对象、活动记录抽屉）、`Workspace.tsx`（房间、focus 随选择变化、他人
变更即时刷新并提示、冲突对话框「以我的为准 / 以对方为准」）；39 条三语言字符串。

**验证**：`test_project_collab.py` 6 项（合并算法 2 项 + HTTP/房间 4 项）与调整后的 `test_services_templates.py` 并发保存用例（六次相同保存 → 一个修订、六个 200）；
Docs 相关套件 48 项通过；三套件全跑见下；typecheck/build/`npm test` 通过；五个守卫通过。浏览器 `ui_e2e_project_collab.py`（隔离实例，两账户同一组织空间，
编辑者手机 390 px）：**Chromium 与 WebKit 各 7 个里程碑全部通过**——双方顶栏互见；owner 选中字段后 editor 的芯片显示「正在看字段「客户名称」」；editor 的
保存即时到达 owner 页面（提示「Editor 新增映射 #2→第二字段（第 2 版）」并刷新）；editor 从旧修订保存另一条映射 → 自动合并（200，`merged_with`）；同一映射
双方从同一修订各改 → 409 `both_changed`；活动记录按人列出含自动合并；owner 关闭页面后 editor 的在场名单清空。截图 `chromium-01…04`、`webkit-01…04`。

**未做**：`SheetEditor` 的共同编辑；映射对话框内的字段级实时锁；活动记录筛选。

### 16.48 表格型模板的协同：在场、单元格级合并、他人保存即时载入（CI-47）

决策记录：`docs/platform/docs-collaboration.md` §4。

**实现**：`builtin_service.merge_sheets`（按工作表 / 单元格 / 字段 / 行列尺寸逐项 / 合并区域做三方合并；`force_ids` 形如 `cell:结果!C1`、`field:<id>`、
`layout:<sheet>`、`sheet:<name>`）；`save_builtin(merge=True)` 只对浏览器路由启用（机器 API 保持整份 409 契约）；内容与已保存相同时不新增版本（`unchanged`）；
版本备注带「已与第 N 版合并」。`collab.py` 对表格型模板开**只承载在场与版本帧**的房间（`writable=False`，不持久化、不记变更日志），版本帧带 `by_id` 以便
各页面忽略自己的操作。浏览器端 `PresenceBar`（从项目页在场组件抽出，标签由页面自算）、`SheetEditor`：房间、活动单元格作为 focus（`结果!B3`）、点击成员芯片跳到其
单元格、他人保存后无未保存修改则自动载入并提示、有则提示「保存时按单元格合并」、409 冲突对话框（单元格/字段/行列/工作表 + 原因）与「以我的为准」；
网格单元格加 `data-ref`；11 条三语言字符串。

**验证**：`test_sheet_collab.py` 4 项（合并算法 2 项：单侧修改/新增/删除、同单元格/字段冲突与强制、行列各自补齐不冲突、同列不同尺寸冲突；HTTP：两人从同一版
各填不同单元格并加字段 → v3 含双方内容且生成的工作簿带两个字段、同单元格 409 带 `conflicts`、`force_ids` 解决、相同内容不加版本、不存在的基线与机器契约仍 409；
表格房间：在场与 focus、他人保存的 `head` 帧、文档帧静默忽略、无持久化）；`test_collab.py` 相应调整。浏览器 `ui_e2e_sheet_collab.py`（隔离实例，两账户同一组织
空间，编辑者手机 390 px）：**Chromium 5 个里程碑全部通过**——双方顶栏互见且编辑者芯片显示 owner 在 B3；owner 保存 v2 后编辑者页面自动载入并点名；编辑者在
v2 上改 C1、owner 保存 v3（B4）后编辑者保存 → 自动合并为 v4（双方单元格都在，owner 页面自动载入 v4）；同一单元格 A2 双方都改 → 编辑者手机弹出冲突对话框，
「以我的为准」得到 v6；版本备注记录两次合并，在场条不超出手机宽度。**WebKit** 同一脚本 5 个里程碑全部通过（截图 `webkit-01…03`）。三套件全跑：2108 通过、0 失败（`pytest-ci47.log`）；typecheck、build、`npm test`、五个守卫通过。

**过程中发现并修复**：(1) 首次证明暴露布局假冲突——编辑器随输入自动补齐行/列，两人各补一维即被判「布局双方都改」；改为行列尺寸逐项合并、合并区域单独判断；
(2) 保存者自己的页面也收到「X 已保存第 N 版」帧（帧先于保存响应到达）——帧带 `by_id`，两个编辑器忽略自己的操作。

**未做**：表格的逐键实时同步（仍是保存即合并）；手机上多条提示叠放遮住顶栏（共享提示组件的既有行为，另记）。

### 16.49 发布链路：验证容器内存耗尽的根因与进程退出时停止后台线程（CI-48）

**现象**：CI-47 的生产运行（`a0c3f18`，run 36236626210）在 verify 作业「Verify the deployment runtime」一步被 45 分钟作业上限取消，
deploy 未运行。容器内 pytest 的进度戳（每 72 项一行）与 CI-46（run 36219721565，同一容器 20 分 29 秒跑完 2105 项）对比：
前 23 段各慢 10–25 %（运行器差异），第 24 段（81 %→85 %，`tests/integration/test_credentials_*`…）**905 秒对 65 秒**，
第 25 段 278 秒对 64 秒，第 27 段超过 280 秒未完成。全程只有 `.` 与一个 `s`，没有失败——是慢，不是错。

**本机复现**（2026-09-26，Docker 28.3.3 arm64，`git archive HEAD` 按接收端方式解包后构建 `infra/Dockerfile` 与
`infra/production/Dockerfile.verify`，`docker run --network none --memory 3g --cpus 2`）：容器在 **34 % 处被 OOM 杀死**
（rc 137，`docker events` 记录 `oom`）；`docker stats` 每 30 秒采样——1.09 GiB/209 PID → 1.68/259 → 1.95/451 → 2.42/496 →
2.81/641 → **2.98 GiB/721 PID**，即每分钟约 1 GiB 线性增长、线程数随测试数增长。容器内 `ps`：Python 主进程 30 秒时已有
150 个线程、RSS 1.1 GB；另有 gpgconf/gpg/gpgsm 僵尸进程（排版引擎每次转换派生，Python 作为 1 号进程不回收）。
GitHub 运行器有交换分区，所以在那里表现为抖动而不是被杀；CI-46 能过只是因为泄漏页面一直是冷页。

**根因**（按测试逐项统计线程增量的插件，本机 Python 3.14 与 3.12 均复现）：每个测试构建的应用都启动守护线程——
`job-runner`、`job-supervisor`、`webhooks`、`project-copies`（Docs），`mail`、`notify`、`auth-sweeper`、`member-import`
（账户服务），以及 `parse_N`（解析线程池）——**从未被停止**。线程持有各自的应用与 `Store`，于是每个测试的整套对象都活到进程结束；
2100 个测试就是数千个线程与数 GB 常驻内存。生产进程不受影响（一个进程一套 worker），但 uvicorn 优雅退出时同样没有停止它们，
也没有把实时协同文档的延迟落盘写出去。

| 项目 | 结论 |
|---|---|
| 成熟参考 | Starlette/FastAPI **lifespan** 契约（应用退出时运行清理，`TestClient` 作为上下文管理器时同样触发；`add_event_handler` 在当前 Starlette 已不存在）；Python `concurrent.futures` 文档：`shutdown(wait=False, cancel_futures=True)` 让空闲工作线程立即退出；Docker `--init`/compose `init: true`（tini 回收僵尸，生产 compose 已有）。 |
| 对比与选择 | (a) 只在 conftest 里按线程名杀线程：拿不到对象引用，做不到；(b) 每个测试改成 `with TestClient(...)`：92 处直接 `create_app` 的测试要逐个改，且不修生产退出；(c) **本方案**：`synwevo_core.workers` 登记表（弱引用，worker 启动时登记自己与线程），`new_api` 的 lifespan 在退出时运行 `api.state.shutdown_hooks`，Docs 与账户服务的 `install` 各追加一个钩子（停 runner/supervisor/dispatcher/copy runner/mailer/notify/sweeper/member-import，`collab.shutdown()` 同步写出所有在编辑的实时文档，`App.close()` 释放解析池）；三个套件的 conftest 各加一个 autouse 夹具在每个测试后 `workers.stop_all(wait=3)` 兜底。 |
| 预期变化 | 生产：uvicorn 优雅退出时 worker 停止、实时文档落盘、租约按既有崩溃路径过期由下一进程接管（无新语义）；测试：每个应用随测试结束释放。CI：两条 `docker run` 加 `--init`；自托管 `infra/docker-compose.yml` 加 `init: true`。 |
| 边界与风险 | `JobService.stop()` 不释放已持有租约（正在执行的切片先完成，其余按 LEASE_S 过期，与崩溃恢复一致）；supervisor 的 `time.sleep(tick)` 改为可被唤醒的 `Event.wait(tick)`；`CopyRunner` 新增 `stop()`；`stop_all` 只等待 3 秒，仍在长切片中的线程会被点名返回。 |
| 验收 | `tests/integration/test_process_hygiene.py`：离开 `with TestClient` 后 8 类 worker 线程全部退出；未用上下文的应用由 `stop_all` 兜底且幂等。`test_collab.py` 新增：两人在房间里、一人刚输入未保存时运行退出钩子，`collab_docs` 立刻写入含该输入的实时文档（head 1、dirty 1）——重启不丢两次保存之间的修改（9.13 E）。三套件 + 线程统计插件：**2110 passed, 1 skipped，10 分 54 秒，结束时只剩主线程**，全程只有 4 次「+1」（正在退出的线程被数到，下一测试即消失）。**同参数容器复跑**（工作树按接收端方式解包重建镜像，`--init --network none --memory 3g --cpus 2`）：**2109 passed, 2 skipped，10 分 26 秒，rc 0**；`docker stats` 20 次采样峰值 **1.50 GiB、PID 最多 9**（修复前 34 % 处 2.98 GiB/721 PID 被杀），每 72 项耗时 1–54 秒、无异常段。内存从 0.56 GiB 缓慢升到 1.50 GiB（进程内缓存与字体/PDF 引擎，未再随线程增长），记为观察项。四项守卫 + `brand_assets --check` + infra unittest 6 项通过。 |
| 发布与恢复 | 无 schema 变化，无前端变化；回滚即回到不停止 worker 的旧行为。CI-47 的代码随本批次一起发布（a0c3f18 未部署）。 |

### 16.50 恢复/撤销/重做不再无声丢掉别人未保存的实时修改；手机提示栈限两条（CI-49）

决策记录见 `docs/platform/docs-collaboration.md` §5。**pytest**：`apps/docs/tests/test_collab.py` 新增 2 项（房间 dirty 时三种版本操作均 409
`live_edits` 且 `detail.online` 点名 Editor/Owner、`GET …/collab/changes` 读到同一状态、`discard_live` 后恢复为 v3 并向双方推送替换与
`head{reason:restore, by:Owner}`、干净后撤销不需标志；最后一人带未保存修改离开后恢复仍 409 且 `last_by=Editor`）；`test_sheet_collab.py`、
`test_template_restore.py`、`test_services_templates.py` 不变通过；SDK 20 项通过（`restore_template_version(discard_live=)`、CLI `--discard-live` 出现在帮助里）。
**浏览器**（`ui_e2e_restore_live.py`，隔离实例，两个真实账户，编辑者 390 px 手机视口）：**Chromium 5/5、WebKit 5/5**——编辑者在手机编辑器输入未保存文字；
owner 在项目页打开「版本」并点「恢复此版本」(v1)，对话框先显示「编辑器里有尚未保存为版本的实时修改（在线：Editor …）。恢复会丢弃这些修改。」且确认按钮为红色
「确认恢复并丢弃实时修改」（截图 `chromium-01`）；确认后 v1 成为 v3，编辑者手机内容随之替换并收到「Owner … 已恢复模板版本（现为 v3）」；编辑者再次输入后
owner 按「撤销模板修改」被拒，提示点名 Editor 并指向版本历史，head 仍为 v3、编辑者文字原样；裸 `POST …/restore` 得 409 `live_edits`。
typecheck/build、四项守卫、`npm test --workspaces` 通过。

### 16.51 活动记录可筛选、标记上次查看后的更改并可跳转；评论经项目房间实时到达（CI-50）

决策记录见 `docs/platform/docs-project-collab.md` §4。**pytest**：`apps/docs/tests/test_project_collab.py` 新增 1 项（编辑者评论、回复、修改，owner 标记解决，编辑者删除——
查看者的连接依次收到 `comment.add/reply/edit/resolve/delete` 帧且 `by` 为真实操作者；活动记录按倒序列出五条、只含评论 id、不含正文）。
**浏览器**（`ui_e2e_activity_live.py`，隔离实例，两个真实账户，编辑者 390 px 手机视口）：**Chromium 5/5、WebKit 5/5**——owner 开着评论抽屉，编辑者发评论后
抽屉在同一秒内出现该评论（不等 30 秒轮询）且顶部提示「Editor … 添加了评论」；活动记录把评论归到编辑者，按成员筛选剩 1 行、按对象「文件」只剩来源行；
点评论行打开评论抽屉并定位到该评论；编辑者再回复后，owner 再开活动记录看到「上次查看后 1 条」、新行着色与分割线（截图 `chromium-03`）；编辑者手机上同一记录无横向滚动。
typecheck/build、`npm test --workspaces`（Docs 45 项含字面翻译资源检查）、设计与组件名守卫通过。

### 16.52 服务重启期间的实时编辑：不丢、能重连、离线输入随重连汇合（CI-51）

决策记录见 `docs/platform/docs-collaboration.md` §6。**pytest**：`apps/docs/tests/test_collab.py` 新增 1 项（进程 1 的退出钩子写出实时文档；编辑者离线继续输入；
同一数据目录的进程 2 从草稿播种（head 1、dirty），owner 连上看到重启前内容；编辑者带本地文档重连后 owner 收到离线输入，双方一致；进程 2 退出时库中为两者之并）。
`Peer` 助手新增 `doc=`（重连带上本地文档）与 `append_text_offline`。**浏览器**（`ui_e2e_restart_live.py`，隔离实例，两个真实账户，编辑者 390 px 手机视口；
脚本真的执行 `platform.py restart docs`，仅可指向隔离实例）：**Chromium 与 WebKit 各 3 个里程碑全部通过**——服务停机 1.2–1.4 秒（SIGTERM → 优雅关闭 → 新进程），
编辑者在停机期间输入的文字在双方重连后出现在 owner 的编辑器里，顶栏仍显示「有未保存为版本的修改」（同一房间而非新房间），随后 owner 保存的 v2 含重启前与停机中输入的全部文字，
编辑者收到「Owner … 已把当前内容保存为 v2」。停机短于客户端首个重连退避，所以「断开」状态在检查前已恢复——脚本如实记录（未伪称看到断线提示）。

### 16.53 命名检查点：保存时给版本起名（CI-52）

决策记录见 `docs/platform/docs-collaboration.md` §7。**pytest**：`apps/docs/tests/test_api_builtin.py` 新增 1 项（说明折叠空白后入历史、默认说明保留、201 字与数组 → 400 `invalid_field`）；
`test_api_docs.py`（OpenAPI 重新生成，`note` 进入保存体 schema）与契约三语言检查通过；SDK 20 项通过，CLI `templates builtin save --note` 在帮助里。
**浏览器**（`ui_e2e_version_note.py`，隔离实例）：**Chromium 4/4、WebKit 4/4**——文档编辑器输入「合同定稿（法务已审）」保存 → v2 且字段清空；项目页「版本」对话框 v2 显示该名、v1 仍为「在软件内创建」；
不带说明再保存 → v3「在编辑器中保存」（API 读回一致）；390 px 手机顶栏含说明字段，不横向滚动（截图 `chromium-03`）。typecheck/build、Docs 前端 45 项、设计守卫通过。

### 16.54 个人项目复制不携带私人协同房间与历史（9.16「生成与复制」回归）

`tests/integration/test_project_copy.py` 新增 1 项：来源项目已有活动记录，来源模板留有未保存为版本的实时草稿（`collab_docs.dirty=1`）；alice 申请、bob 批准、
执行器完成后——目标项目的 `project_activity` 为 0 条，新模板没有 `collab_docs`/`collab_changes` 行，副本只有 v1（来源 head 版本，不是草稿），来源草稿原样保留。
通过（本地）。

### 16.55 变更记录显示块内的插入与删除；schema v22（CI-53）

决策记录见 `docs/platform/docs-collaboration.md` §8。**pytest**：`apps/docs/tests/test_collab.py` 新增 1 项（连续输入合并为一行，`before`=首次按键前的块文本
「客户：【客户】」、摘录=「客户：第一次第二次【客户】」、count 2；插入块 `before` 为空）；`test_migration_v3/v4` 不变通过。**前端**：`tests/diff.test.ts` 4 项（Docs 前端 49 项通过）。
**浏览器**（`ui_e2e_change_diff.py`，隔离实例——重启时完成 v21→v22 迁移——两个真实账户，编辑者 390 px 手机）：**Chromium 4/4、WebKit 4/4**——编辑者在段末追加「 +手机补充」，
owner 把段首「注段」换成「忘录」；owner 的变更记录里编辑者一行是一个插入串，owner 一行显示「注段」删除并列「忘录」插入、前后文字不变（截图 `chromium-01`）；
`GET …/collab/changes` 返回 owner 行的 `before`=「备注段 +手机补充」、摘录=「备忘录 +手机补充」；编辑者手机同一记录无横向滚动。
过程中发现证明脚本用 Home/方向键/退格在 ProseMirror 里删字不可靠（退格未生效），改为显式 DOM 选区后输入；他人光标标签在段落内，文本断言改为跨标签匹配。
顺带：`hello` 帧的在线名单过滤掉自己的其他设备（CI-52 手机截图里同一人出现第二个芯片）；半开连接的清理依赖 uvicorn 的 WebSocket ping 默认值（`--ws-ping-interval 20`、`--ws-ping-timeout 20`，已核对帮助输出），生产启动命令未覆盖这两项。

### 16.56 版本对话框列出尚未保存为版本的实时修改（CI-54）

`lib/changes.tsx` 抽出编辑器变更记录的文字与差异渲染供版本对话框共用；对话框在房间 dirty 时显示可展开的「尚未保存为版本的实时修改（N 条）」（与 CI-49 警告并列）。
**浏览器**：`ui_e2e_restore_live.py` 第 2 里程碑扩展——展开后第一行为「Editor … 修改了正文第 4 段（N 次）」，插入串为手机上输入的「——手机上未保存的补充」（截图 `chromium-01`）；
**Chromium 5/5、WebKit 5/5**。typecheck/build、Docs 前端 49 项、设计守卫通过；无后端改动。

### 16.57 有界更新包、损坏草稿恢复、远端可见延迟（CI-55）

决策记录见 `docs/platform/docs-collaboration.md` §11。**pytest**：`test_collab.py` 新增 2 项（5000 字节帧在 4096 上限下 → 该连接 4400 `frame_too_large`，owner 收到 `left` 后照常编辑；
`collab_docs.state` 写入垃圾字节后连接 → `hello.recovered=true`、head 1、dirty false、内容为 head 版本，随后编辑与离开把该行修复为可读状态）；协同三套测试 26 项通过。
**前端**：`hello.recovered` 进入房间状态，编辑器一次性提示「之前未保存的实时修改无法读取，已改用第 N 版」；4400 的 `frame_too_large`/`template_deleted` 用人话显示；typecheck/build、Docs 前端 49 项通过。
**测量**（`ui_e2e_collab.py`，同一台 Mac、回环、两账户、模板级文档）：最后一次按键后对方可见——Chromium 桌面→手机 1 ms、手机→桌面 2 ms；WebKit 2 ms、8 ms（16 个里程碑全部通过）。这是本地上限，公网以 RTT 为主。

### 16.58 项目设置变更显示前后值（CI-56）

`update_project` 的变更帧与活动记录带 `before`/`after`（仅真正变化的名称/样本规则/输出/模板/固定版本），前端按字段读成人话（`docs-project-collab.md` §5）。
**pytest**：`test_project_collab.py` +1（改名 + 样本规则：`before.name`「协同项目」→`after.name`「改名后的项目」，`before.record_rule.mode` per_row → `after` `{mode: per_file}`，未改的 `output` 不在 `after` 里；活动记录同样）。
**浏览器**：`ui_e2e_activity_live.py` 新增里程碑——owner 改样本规则后活动记录第一行读作「样本规则：未设置 → 每个文件一份」（截图 `chromium-05`）；**Chromium 6/6、WebKit 6/6**。typecheck、Docs 前端 49 项通过。

### 16.59 里程碑回归：Docs 主链路（桌面/手机）与外壳；无账户部署的模板选择器被送去不存在的登录页（CI-57）

协同 12 个批次（CI-45…56）发布后按 §四「重大里程碑才做必要的跨模块回归」跑既有回归证明。**发现**：`apps/docs/scripts/ui_e2e.py` 与 `ui_e2e_mobile.py`
（各自起 `SYNWEVO_UI_AUTH=none` 的一次性实例，即无账户部署模式）都在「设置模板」后失败——浏览器控制台：`GET /api/organization-templates` 401，
随后页面被送到 `/auth/start?next=/p/…`（auth-client 把服务端确认的 401 `unauthorized` 当作会话失效）。根因：模板选择器在 CI-3x 加入「组织共享模板」页签后，
无账户模式下没有会话，`_uid()` 一律回答 401「请先登录」，而该模式没有登录页。生产用的是 session 模式，不受影响；无账户/basic 模式的自托管部署受影响。
**修复**：`_uid()` 在非 session 模式改答 403 `accounts_disabled`（"此部署未启用账户，这个功能需要登录的成员"，三语言），`GET /api/organization-templates`
在该模式返回空列表（选择器不显示该页签、无错误横幅）；auth-client 不会把 403 当作会话失效。**验收**：`test_api_builtin.py` +1（none 模式：共享模板列表为空 200、
评论 403 `accounts_disabled`）；重跑两条主链路证明：桌面「UI E2E passed in 11.6s」（100 份文档生成并读回），手机「phone flow ok in 9.7s: 100 documents generated and read back at 390×844」。
外壳回归（`ui_e2e_shell.py`，需要一个已完成引导且空间里有待接受邀请的受控账户——脚本前置条件，不是产品缺陷）：**Chromium 与 WebKit、桌面与 375 px 全部通过**——抽屉焦点圈闭、账户页返回链接、组织页刷新不重复探测会话（0 次）、成员最后一行菜单在视口内、门户重获焦点不回退到加载态、公开首页加载中不闪「登录」、Docs 手机面板切换、0 条 CSP 违规。
**§9.10 矩阵的其余回归**（同一隔离实例、受控账户）：`ui_e2e_messaging.py` Chromium 6/6（私聊经流到达与徽章、手机端引用回复与表情实时到桌面、图片附件经安全检查按内容哈希存一份并内联、
群创建/阅读/移出后历史 404 且搜索无结果、0 CSP/页面错误）；`ui_e2e_project_copy.py` Chromium 5/5（申请显示清单且无自批、owner 手机宽度带理由批准并被要求重新输入口令、
副本在目标空间预览/生成 2 份并读回 ZIP、原项目不变且对审批者不可见、副本在个人空间不可达）。`ui_e2e_member_import.py` Chromium 5/5（自定义角色创建、工作簿解析与表头映射建议、角色列需显式映射、预览 邀请 1/跳过 2/阻断 1、带理由与重认证确认后执行、CSV 报告下载、新人接受邀请后恰好拥有导入角色的权限并收到收件箱通知、0 CSP/页面错误）。
其余 14 条 round-8 证明按顺序重跑（Chromium）：**通过** `ui_e2e_org_templates`、`ui_e2e_push`（`/sw.js`、manifest、推送订阅）、`ui_e2e_preflight_fix`、`ui_e2e_link_cards`、
`ui_e2e_moderation`、`ui_e2e_docs_upgrades`、`ui_e2e_template_versions`（含 CI-49 后的恢复路径）、`ui_e2e_phone_copy`、`ui_e2e_comments`、`ui_e2e_component_names`（简繁英无底层组件名）。
`ui_e2e_challenge` 未跑：本隔离实例启动时未配置 Turnstile 测试密钥（脚本首行断言 `challenge not configured`），上次验证见 §16.2x；不改运行中的隔离实例来凑数。
`ui_e2e_roles` / `ui_e2e_onboarding` / `ui_e2e_inbox` 需要预置账户与组织（批量跑时参数错误），单独补跑——期间隔离实例的注册限流按设计生效（同一地址短时多次注册 → 429 `too_many_attempts`，`retry_after` 788 秒），
等窗口过后再跑：`ui_e2e_roles` Chromium 5/5（自定义角色只含持有者可授的权限、分配与有效权限来源、成员恰好拥有两项权限并在收件箱看到英文通知、删除已分配角色提示影响 1 人）；`ui_e2e_onboarding` Chromium 8/8（注册→验证邮件→引导→个人空间→邀请→接受→登出）。`ui_e2e_inbox` 首跑在偏好表「安全」行的断言处 strict-mode 失败：该行自 Web Push 起有两个渠道复选框（站内、推送），脚本只预期一个——脚本过时，不是产品缺陷；改为断言该行全部复选框均锁定后 Chromium 9/9（未读计数 2→1→2→0、抽屉与 API 首项一致、偏好保存、门户与 Docs 从各自源站显示同一未读数与首项）。

### 16.60 登录后的落点：无目标的登录落到平台，不停在账户设置页（CI-58）

**问题与基线**：用户反馈（2026-09-27）「登录完之后直接跳转的是 accounts.wegreen.ltd/account，所有的逻辑存在重大错误」。复现（代码与隔离实例）：
`Login.tsx` 的 `next = safeNext(query.next)`，没有 `next` 时是 `/`；`signedInDestination` 原样返回 `/`；账户服务的 `/` 路由把已登录者 `Navigate` 到 `/account`。
走到这条路的入口：直接打开 `accounts.wegreen.ltd/login`、`/logged-out` 页的「重新登录」、忘记口令后重新登录、书签。从门户「登录」（`/auth/start?next=/workspace`）或
Docs 发起的登录经 `/login?next=/oidc/authorize…` 回到发起方，本来就不受影响（`ui_e2e_shell.py` 门户段一直覆盖）。生产 `/api/auth/state` 公开字段确认
`portal_url = https://synwevo.wegreen.ltd`（只读检查，2026-09-27），引导页的 `portalWorkspaceUrl` 因此不在受影响范围。
**成熟参考**：Microsoft Entra 登录门户把已登录者放到应用启动页 My Apps（learn.microsoft.com `entra/identity/enterprise-apps/myapps-overview`，
"When signed in to the My Apps portal, the applications that are made visible are shown"，访问 2026-09-27）；账户资料页是另一处。OIDC RP 发起的登录回到 RP 的 `next`（已有行为）。
**对比与选择**：(a) 无目标登录改落平台，经门户 `/auth/start` 交接（选）；(b) 保持 `/account` 加显眼「进入平台」按钮——与用户要求相反；(c) 直接链到门户公开首页——门户还要再回账户服务拿会话，多一跳且首页不是工作空间。
**预期变化**：无目标登录与账户根路径 → `<portal_url>/auth/start?next=/workspace&lang=<界面语言>` → 门户拿到自己的会话 → `/workspace`；显式 `next`（含 `/account`、`/oidc/authorize…`）、
邮箱验证/邀请/引导的优先级、无门户部署（`portal_url` 空 → `/account`）都不变；`/logged-out` 增加「返回平台首页」次级入口。
**边界与风险**：门户地址只来自服务端 `portal_url`，不来自查询参数或 Referer——不构成开放重定向（`safeNext` 继续只放同源路径）；配置成非 http(s) 绝对地址时
`platformHandoffUrl` 抛错（引导页按钮显示为错误，与既有 `flow.validation` 契约一致），登录页的 `platformHomeUrl` 捕获后回退 `/account`，不让配置错误拖垮登录页；
不改 Cookie 作用域、不改 RP ID、不涉及数据迁移；三语言新增两条文案（「正在前往平台…」，`/logged-out` 复用「返回平台首页」）。
**验收**：`packages/auth-client/web/tests/platform-handoff.test.mjs` 5 项（origin 替换配置里的路径、参数顺序 `next/workspace_id/lang`、空配置为 `null`、
相对/非 http(s)/`javascript:` 配置抛错）；`flow.validation.mjs` 19 项（含原有「相对配置必须抛错」）；`npm run typecheck && npm run build` 通过；
`ui_e2e_shell.py`（隔离实例，受控账户）**Chromium 与 WebKit、桌面 1280 与手机 375 px 四组全部通过**：无目标登录 → `portal.localhost:31769/workspace`（不是登出态视图）、
账户根路径 `/` → 门户 `/workspace`、`/login?next=/account` → 账户页；其余外壳断言（返回链接、抽屉焦点圈闭、菜单键盘、末行菜单、门户重聚焦、Docs 面板）不变，0 CSP 违规。
八条在账户服务直接登录的证明（inbox / shell / roles / messaging / member_import / institutional_mail / onboarding / mobile_audit）的等待条件从「离开 `/login`」改为
「交接完成」（不在 `/login`、`/auth/start`、`/auth/callback`、`/oidc/`），否则下一步 `goto` 会和交接中的导航竞争。改后复跑 `ui_e2e_roles.py` Chromium 5/5 与 `ui_e2e_inbox.py` Chromium 9/9（读者换成刚收到角色通知的受控成员——脚本前置条件），
均通过；机器路径不受影响，同日 `python_full_flow.py`（sdk/cli/person，24 份文档、52 份内置输出）与 `ts_run.py --builtin-only`（12+12）在新鲜隔离服务上通过。
**发布与恢复**：纯前端（账户外壳 + 共享 auth-client），无 schema 变化；回滚即重新部署上一镜像。**已部署**：`ad2eeeb`，run 36313988930（verify 与 deploy 成功，backup 20260927T111919Z）；线上只读核对见 deployment.md（新外壳块携带新文案与交接代码、三源 readyz、`portal_url` 已发布）；线上登录行为核对需要受控生产账户——本工作站没有，账户持有人下次直接登录即是核对。

### 16.61 退出登录的落点：竞态，不是引擎差异（CI-59）

**问题与基线**：候选队列自 CI-19 起记着「Chromium 登出后到 `/login`、WebKit 到 `/logged-out`（两者 `/api/auth/me` 都 401），核对是竞态还是引擎差异」。
读代码即可定性：`signOut()` 在 `POST /api/auth/logout` 成功后先 `invalidate()`（清空会话、推进代际），再 `window.location.replace("/logged-out")`；
`invalidate()` 触发的重渲染让 `/account` 下的 `RequireSession` 看到 `me === null`，随即 `window.location.assign(signInUrl(state, back))`——第二个导航。
Chromium 让后发的导航取代仍在挂起的第一个，于是落到 `/login?next=%2Faccount…`；WebKit 先提交了 `/logged-out`。这是竞态。后果在 CI-58 之后更明显：
「已退出」页上的「重新登录」「返回平台首页」Chromium 用户从未见到，再登录时显式 `next=/account` 又把人带回账户页。
**成熟参考**：OIDC RP-Initiated Logout 1.0 的语义是退出后到达明确的登出后页面（`post_logout_redirect_uri`），账户服务自己的 `/oidc/logout` 也这样做；
本地按钮的退出应与之一致。
**对比与选择**：(a) 去掉 `invalidate()`——不行，代际推进是「旧响应不得写回」的保证（既有测试「sign-out clears context, rejects old deliveries」）；
(b) 先导航再 `invalidate()`——顺序无关，第二个导航仍会发出；(c) 让 `RequireSession` 知道正在退出（选）：模块级 `signingOut`，`signOut()` 与跨标签页登出事件置位，
守卫对退出中的空会话渲染「正在退出…」而不重定向；过期会话（`synwevo:session-expired`）不置位，照旧去登录页并带返回地址；`POST` 失败复位。
**验收**：`session-generation.test.mjs` +2：退出后守卫渲染 `PageState`「正在退出…」且 `navigation` 只有一次 `replace('/logged-out')`；过期会话的守卫 `assign('/login?next=%2F%3Ftab%3Dsecurity&lang=en')`。
`npm test --workspaces` 38/38（auth-client）；三语言新增「正在退出…」；`ui_e2e_onboarding.py` 第 6 步改为两引擎都必须到 `/logged-out`——隔离实例复跑：**Chromium 8/8 与 WebKit 8/8**（注册 → 验证 → 引导 → 交接门户 → 邀请 → 接受 → 退出），
两引擎「signed out → /logged-out; /api/auth/me 401」，0 页面错误、0 CSP 违规；改前同一脚本的 Chromium 记录是 `/login`。导出树（index）typecheck/build/`npm test` 通过。
**发布与恢复**：共享 auth-client，三个外壳同时受益；无 schema 变化，回滚即上一镜像。**已部署**：`8184dc2`，run 36315903038（verify 与 deploy 成功，backup 20260927T115350Z）；线上只读核对：三源 readyz、账户外壳共享块携带「正在退出…」。

### 16.62 依赖方的「退出登录」结束平台会话（CI-60）

**问题与基线**：核对 CI-59 时读到依赖方的退出链路：共享 `signOut()` 在 Docs/门户上调 `POST /api/auth/logout`——依赖方的这条路由只撤销本应用会话
（`session_routes.py::logout`：`revoke_device` + 删本源 Cookie），然后 `location.replace("/logged-out")`；依赖方的 `/logged-out` 由 `build_account_redirect_router`
303 到账户服务的 `/logged-out`，那一页写着「当前设备的统一登录与关联应用会话已退出」。此时账户服务的会话仍然有效：点「重新登录」→ `/login` 已登录 →（CI-58）
交接回工作空间，不需要口令；共享电脑上下一个人打开门户仍是登录态。CI-59 之前 Chromium 更糟——守卫的 `/auth/start` 竞态直接把人静默重新登进 Docs，看起来像「退出没反应」。
依赖方早有 `POST /api/auth/sign-out?everywhere=true`（返回账户服务 `/oidc/logout` 地址，`test_sso_chain.py` 覆盖），但没有任何界面调用它。
**成熟参考**：统一账户在任一产品退出即退出该账户（OIDC RP-Initiated Logout 1.0：RP 把浏览器送到 OP 的登出端点并以 `post_logout_redirect_uri` 回到自己）；
账户服务自己的「退出登录」已经是平台级（中央会话 + 后通道登出各应用），依赖方与之不一致才是缺陷。
**对比与选择**：(a) 保留「只退本应用」但改文案——共享电脑的安全预期仍落空；(b) 依赖方退出改为平台级（选）：`signOut()` 按 `state.role` 分流，app/portal 走
`sign-out?everywhere=true` 并跟随返回的登出地址，accounts/all 不变；(c) 在依赖方加「仅退出本应用 / 退出全部」两个按钮——增加决策负担，且「仅退本应用」的价值在单账户平台上不成立。
**预期变化**：Docs 点「退出登录」→ Docs 会话撤销 → 账户服务 `/oidc/logout`（中央会话结束、后通道通知门户等）→ 回到 `docweave…/signed-out`（新页：已退出 / 重新登录=本源
`/auth/start` / 返回平台首页）；门户同样有 `/signed-out` 页（门户本身没有退出控件，经「账户」页退出，那条路一直是平台级）。`/api/auth/sign-out` 允许匿名调用：无本地会话时
不撤销任何东西、只返回登出地址（issuer 与 client_id 都是公开标识），使本应用会话已过期的页面也能结束平台会话；有会话时仍查 CSRF（`session_mutations`）。
**边界与风险**：不改 Cookie 作用域与 RP ID；登出地址由依赖方服务端按登记的 `post_logout_redirect_uri` 生成，账户服务精确匹配登记表，未登记的 400 不跳转（既有契约）；
三语言新增三条共享文案（与账户服务同词）；单进程 `all` 角色与账户服务的退出路径不变。
**验收**：auth-client 单测 +1（role=app：退出发出 `sign-out?everywhere=true`、导航到返回地址、不再调 `/api/auth/logout`），39/39；
`test_sso_chain.py` +1（匿名 everywhere → 200 + 登出地址 → 最终 `/signed-out` 且中央会话结束；匿名普通变体 `{ok, scope: app}`），文件通过；
`npm run typecheck && npm run build`、四项守卫、`npm test --workspaces` 通过；`ui_e2e_shell.py` 桌面段新增：Docs 点「退出登录」→ Docs `/signed-out` 页可见「已退出」、
Docs 与账户服务的 `/api/auth/me` 都 401、「重新登录」到账户服务登录页并要求口令——隔离实例复跑 **Chromium 与 WebKit 都通过**（其余外壳断言与 CI-58 的落点断言不变，0 CSP 违规）。
**发布与恢复**：共享 auth-client + 两个依赖方外壳 + core 匿名路径表；无 schema 变化，回滚即上一镜像。**已部署**：`33c3e1f`，run 36318459107（verify 与 deploy 成功，backup 20260927T123955Z）；线上只读核对见 deployment.md。

### 16.63 门户页头的「退出登录」（CI-61）

**问题与基线**：`Portal.tsx` 已登录页头只有「账户与安全 / 平台管理 / API 管理」和头像，手机端的「账户菜单」同样没有退出项——退出只能绕到账户服务的账户页或 Docs。
§五/§八把「退出」列为完整任务链的收尾；CI-60 之后共享 `signOut()` 在依赖方上已是平台级，门户缺的只是入口。
**成熟参考**：平台首页的账户菜单含退出，是 Microsoft 365 首页、Google Workspace、Atlassian Home 等公开界面的一致做法（按公开界面观察，非内部实现断言）。
**对比与选择**：(a) 桌面在 `.pt-account` 加文字按钮 `button.pt-link`（与旁边链接同样式，真按钮而非链接：退出是动作不是导航），手机在「账户菜单」加菜单项（选）；
(b) 只加手机菜单项——桌面仍要绕路。均复用共享 `session.signOut()`，不另写退出逻辑。
**预期变化**：门户页头点「退出登录」→ 门户会话撤销 → 账户服务 `/oidc/logout`（中央会话结束、后通道通知 Docs）→ 门户 `/signed-out`（CI-60 加的页）。
**边界与风险**：无接口变化；文案复用共享目录「退出登录」（三语言）；`check_design` 通过（按钮重置只用继承字体与已有令牌）。
**验收**：`ui_e2e_shell.py` 手机段新增：打开「账户菜单」→「退出登录」→ 门户 `/signed-out` 页「已退出」可见、门户与账户服务 `/api/auth/me` 都 401、「重新登录」到账户服务登录页并要口令；桌面段保留 Docs 退出检查——隔离实例复跑 **Chromium 与 WebKit 四组全部通过**（桌面 Docs 退出、手机门户退出、CI-58 落点断言、其余外壳断言，0 CSP 违规）；四项守卫与 `npm test --workspaces` 通过。
**发布与恢复**：门户外壳；无 schema 变化，回滚即上一镜像。**已部署**：`e900774`，run 36320325701（verify 与 deploy 成功，backup 20260927T131414Z）；线上只读核对见 deployment.md。

### 16.64 测量记录：Docs 列表页的共享块 `perm` 里是什么（候选项关闭）

候选队列自 CI-11 起记着「列表页与工作台共用的视图层可再拆（274 KB）」。2026-09-27 生产只读测量：Docs `/` 下载 `index-CmVnbLA2.js`
238 KB（gzip 74 KB）+ `perm-Bl4dqY9f.js` 324 KB（gzip 109 KB）+ 运行时 0.6 KB。用带 source map 的临时构建（`vite build --sourcemap --outDir <scratch>`）
按 VLQ 归因 `perm` 的 247 KB 可映射字节：`apps/docs/web/src/messages.ts` 114.8 KB（46.5%）、react-router 38.2 KB（15.5%）、`messages.server.ts` 26.7 KB（10.8%）、
`packages/ui/src/ui.tsx` 11.2 KB、react 8.2 KB，其余为 auth-client 会话/铃铛/API 层与 UI 基础件，均为列表页真实使用；`Workspace`（222 KB）、`DocEditor`（521 KB）、
`collab`（98 KB）已各自成块。结论：该块没有可拆出的视图层；唯一的进一步瓶颈是三语言字串目录随每页下载，拆法是按当前语言懒加载译文表
（zh-Hans 用户约省 70 KB 原始 / 30 KB gzip，切换语言时多一次请求），改动触及 `registerMessages` 的同步语义，收益不抵风险，**不实施**；候选项关闭，
如未来列表页首屏预算收紧再评估。

### 16.65 「← Synwevo」回到工作空间，不回公开首页（CI-62）

**问题与基线**：用户反馈（2026-09-27）：在 `docwave.wegreen.ltd/` 点顶栏「← Synwevo」回到的是 `synwevo.wegreen.ltd/`（公开首页），要回到工作空间。
代码：共享 `PlatformNav` 的返回链接 `href={state.portal_url}`，账户外壳 `Topbar` 的品牌链接 `link(state?.portal_url || "/")`——两处都指向门户根。
**参考与选择**：与 CI-58 同一个决定——平台的「家」是工作空间；沿用门户自己的 `/auth/start?next=/workspace` 交接（门户在同一跳里拿到会话，会话过期时也不会停在「欢迎回来」）。
**预期变化**：Docs 顶栏「← Synwevo」与账户外壳品牌链接 → 门户 `/workspace`；标题/无障碍名改为「返回工作空间」（三语言）；没有门户配置的部署保持原回退。
**验收**：`ui_e2e_shell.py` 桌面段：链接 href 含 `/auth/start` 与 `next=%2Fworkspace`，点击后到门户 `/workspace` 且仪表盘可见——隔离实例复跑 **Chromium 与 WebKit 都通过**（CI-60/61 的退出断言与其余外壳断言不变）；auth-client 单测 39/39、字串校验 19/19、四项守卫与 `npm test --workspaces` 通过。
**发布与恢复**：前端两处；无 schema 变化，回滚即上一镜像。**已部署**：`41e6f4f`，run 36323605133（verify 与 deploy 成功，backup 20260927T140828Z）；线上只读核对见 deployment.md。

### 16.66 门户工作空间的「消息」入口（CI-63）

**问题与基线**：用户反馈（2026-09-27）：`synwevo.wegreen.ltd/workspace` 没有消息入口。代码：门户 `Shell` 页头只有账户链接、铃铛与语言菜单（`Portal.tsx`），
Docs 的平台栏则有 `MessagesLink`（`PlatformNav.tsx`）。**选择**：复用共享 `MessagesLink`（读共享表 `/api/auth/messaging/unread-count`，60 s 轮询 + 事件即时更新），
放在铃铛旁，只在已登录时渲染；目标取自 `state.account_url`（消息独立成应用后改为注册表地址，见第九轮）。
**验收**：`ui_e2e_shell.py` 门户段：入口可见、href 以账户源开头并以 `/messages` 结尾、点击后到消息页——隔离实例复跑 **Chromium 与 WebKit、桌面与 375 px 四组全部通过**（CI-60…62 的断言不变，0 CSP 违规）；四项守卫与 `npm test --workspaces` 通过。
**发布与恢复**：门户外壳；无 schema 变化，回滚即上一镜像。

### 16.67 消息未读徽标在每个源真实生效（CI-64）

**问题与基线**：第九轮盘点（新源可行性）发现：`MessagesLink` 从所在源轮询 `/api/auth/messaging/unread-count`；共享网关对 app/portal 角色放行该路径
（`http.py` APP_ACCOUNT_PATHS），但处理函数只由账户服务的消息路由挂载，于是生产的 Docs 与门户源由外壳兜底答 404，`useMessagesUnread` 吞掉错误、徽标永远为 0。
单进程/本机（角色 all）看不出来，所以此前的浏览器证明都没抓到。**修复**：路由移入共享 `synwevo_auth_client.session_routes`（每个角色各挂一份的既有模式），
账户服务的消息路由删除重复项。**验收**：`tests/integration/test_sso_chain.py` +1（Docs 源登录后 200，含 `unread` 与 `conversations`；匿名 401）；
`ui_e2e_shell.py` 门户段断言门户源 200。**发布与恢复**：无 schema 变化，回滚即上一镜像。

### 16.68 Accounts 的返回导航（CI-65）

**问题与基线**：用户反馈「Accounts 界面没有返回工作空间或上一级的按钮」。核实：`Topbar` 只在有来源提示时显示一个返回链接（从 Docs 进入时是「返回 Synwevo Docs」，
没有「工作空间」）；`/organizations` 与 `/organizations/:oid` 借用登录页框架 `AuthFrame`，没有账户导航，只有页内文字链接；子页面没有确定的父级。
**选择**（brief §6）：导航由产品路径决定，不用 `history.back()`——顶栏常驻「返回工作空间」（`platformHandoffUrl(portal_url, {next:"/workspace", workspaceId})`，
门户在同一跳里拿到会话并恢复当前空间；手机也在栏内而非只在抽屉），来源应用链接保留（「返回 Synwevo Docs」），子页面按路径显示真实父级
（`parentOf`：组织详情 → 组织、成员/恢复/审批 → 账户与安全、API 管理 → 平台管理、会话 → 消息）；组织列表与详情改用账户外壳（`Topbar` + `acct-page`），
删除页内重复的「账户设置 / 工作空间 / 返回我的组织」链接（全站文案精简的一例）。
**验收**：`ui_e2e_shell.py`（隔离实例，两引擎，1280 与 375 px）：工作空间链接指向门户 `/auth/start` 且带 `next=%2Fworkspace`；所有返回链接只指向服务端列出的源；
新开页面（无历史）直接打开组织详情页 → 「组织」父级链接可见并回到列表；直接打开成员页 → 「账户与安全」父级链接可见并回到账户页；四组通过。
`tsc`、`flow.validation` 19/19、四项守卫、`npm test --workspaces` 通过；`tests/integration` + `services/identity/tests` 1235 通过（与 CI-64 同批）。
**发布与恢复**：账户外壳；无 schema 变化，回滚即上一镜像。

### 16.69 域名迁移盘点与准备（CI-67）

**盘点**（2026-09-27，全仓库）：产品地址无一写死在代码里——issuer（`oidc.py:90-96`）、RP 回调（`rp.py:60-66`）、门户交接（`session_routes.py:58`）、邮件链接
（`mail.py`，取 `SYNWEVO_PUBLIC_URL`）、注册表 `launch_url`（Docs 进程每次启动按 `SYNWEVO_APP_URL` 改写）、通知目标（按源名在读取时解析）、
Passkey RP ID（`passkeys.py:43-50`，凭证按 RP ID 绑定，`usable_here` 提示已存在）、Turnstile 主机名、Cookie 仅主机、CSP 无第一方主机名、机器凭据与 Webhook
不绑定主机。绑定主机的是：`infra/production/compose.yaml`（env）、`nginx.conf`（server_name）、`.github/workflows/production.yml`（健康检查）、
数据库里的 `oauth_clients` URI（CLI 幂等更新）、外部控制台（隧道/证书/Turnstile/IdP）与 16 份文档。
**决定**：`SYNWEVO_RP_ID=accounts.synwevo.com`（与账户主机一致；旧 Passkey 需重新登记，账户页已有提示；不采用需长期保留旧主机的 Related Origin Requests）；
旧主机 308 永久重定向（保留路径与查询串；308 保留方法）；`workplace.synwevo.com` 作为工作空间别名（根 → `/workspace`）；发件域不变。
**产出**：compose/nginx 参考文件、CI 健康检查主机变量化（`SYNWEVO_PUBLIC_HOSTS`，未设即旧主机，切换不需推代码）、口令黑名单、`docs/platform/domain-migration.md`
（映射、切换步骤、用户影响、回滚、待运维事项）。**验证**：`nginx -t`（`nginx:1.28-alpine`，`--add-host` 解析上游）通过；口令黑名单 Python/TS 双侧同步、`tsc` 通过。
**未完成（线上）**：切换本身是运维在 VPS 的一次性步骤（安装两份配置、`client register` 新 URI、重建三角色与网关、设仓库变量）；
`message.wegreen.ltd` 的隧道主机名待确认。

### 16.70 私信邮件缺省关、只寄未读摘要（CI-68）

**复现**（2026-09-28，代码）：`default_preferences()` 把 conversation 的邮件设为 `digest`，账户「通知」页允许选「立即」；`NotificationDispatcher.run_once` 把到期投递直接寄出，
不看读者此刻是否已读、消息是否撤回、会话是否静音/退出；摘要条目正文是发消息时截的 120 字原文；`preview` 偏好只存不用。与第九轮 §8.3 的四条要求都不符。
**修复**：见 issues.md CI-68。**验证**：`services/identity/tests/test_messaging.py::test_private_message_mail_is_off_by_default_and_an_opt_in_digest_of_what_is_still_unread`
（默认 → 收件箱有条目、投递 `skipped/channel off`、outbox 空；`immediate` → 400「不逐条」；开启摘要 → 一条待寄，撤回其中一条后寄出的正文是「有 1 条未读消息」且不含两句原文；
下一跨度读过 → skipped，再下一跨度在 30 分钟内 → hold 到上一封 +30 分钟；`preview=full` → 附「第四句」；静音 → 不产生条目）；
`test_notifications.py::test_v23_resets_conversation_mail_saved_under_the_old_default_to_off`（v22 库带 digest 行 → 重开为 off，docs 类别/免打扰/预览不变；行里的 immediate 读作 digest）。
套件：identity 全量 exit 0（`-q` 配置下无计数行，进度行 638 个通过点，无 F/E）、integration 全量 exit 0、Docs 全量（本节末尾）；`npm run typecheck`、account-web 19 项契约验证、三外壳 build、导出树复核（archive HEAD：typecheck/build/`npm test --workspaces` 19+39+49+19 通过）、infra 6 项 unittest 通过。
**隔离实例实测**（2026-09-28，账户服务以新代码重启，v23 迁移在该库执行）：`GET /api/auth/notifications/preferences` → conversation `off`、preview `minimal`；`PUT` conversation=immediate → 400「消息不逐条发邮件：只能选 digest（未读摘要）或 off」；digest/off 往返正常；`ui_check_notifications.py`（Chromium，1280 与 390 宽）：「消息」行的邮件下拉只有「不发邮件 / 未读摘要」、默认「不发邮件」，其它类别仍三档，规则说明可见，「邮件与推送里的消息内容」默认「只显示会话与条数」（截图 `ci68-notifications-{desktop,phone}.png`）。
**未验证**：真实 SMTP 收件（规则在 outbox 之前，与投递链路无关）；生产库里已有偏好行的数量（迁移在部署时执行，`_conversation_mail_becomes_opt_in` 幂等）。

### 16.71 Docs 外壳文案精简（CI-69）

worktree（`dfaf324`，基于 0c43018）内由子代理完成并验证：三外壳 typecheck/build、`npm test -w @synwevo/docs-web` 49/49、组件名/设计/品牌守卫、
`apps/docs/scripts/ui_e2e.py`（桌面 Chrome）、`ui_e2e_mobile.py`（Chromium 390×844）、`E2E_ENGINE=webkit ui_e2e_mobile.py` 通过。
主分支 cherry-pick 为 `a084f48`；合并后的复核结果记在本节末尾。保留未动的项与理由见 `docs/platform/docs-copy.md`。

### 16.72 域名切换后的 Passkey 登录解释（CI-70）

**复现**（2026-09-28，代码）：`auth_begin` 匿名、不列 `allowCredentials`（避免枚举账户），切换 RP ID 后浏览器对旧凭证报 `NotAllowedError`，`Login.tsx` 只显示
「已取消，或没有可用于本站的安全密钥。」。**修复**：见 issues.md CI-70。**验证**：`services/identity/tests/test_passkeys.py::test_the_sign_in_screen_learns_that_passkeys_live_on_another_domain`；
identity 套件全量 exit 0（本节末尾）；`npm run typecheck`、account-web 19 项契约验证通过。**未验证**：真实浏览器在两个主机间切换（需要切换后的生产或双主机隔离实例；切换当天由受控账户实测）。

### 16.73 公开页面重写（CI-71）

子代理在 worktree（`c05ad93`，基于 0c43018）内完成并验证：`npm run typecheck && npm run build`、`npm test --workspaces --if-present`、五个守卫、`tests/integration` 593 通过
（`test_portal_public.py` 两个写死旧文案/旧 sitemap 的用例按预期失败，未改测试）；隔离实例（`.scratch/data`，ports 31865/31869/31870）Playwright：
Chrome + WebKit × 1280/375 × zh-Hans/zh-Hant/en × home/privacy/service = 36 组通过（0 控制台错误、0 CSP 违规、375 宽 0 px 横向溢出、无占位文字、
title/lang/canonical 逐页逐语言正确、未登录页头「登录」→ `/auth/start?next=%2Fworkspace`、所有 CTA/页脚链接可达、扫描器状态文字按运行时渲染）；
登录态：页头加载期间只见「进入工作空间」，CTA → `/workspace`。截图 45 张在该 worktree 的 `.scratch/shots/`。
主分支 cherry-pick 为 `460eaee`；`test_portal_public.py` 改为从 `COPY` 读期望并加 privacy/service 六行参数化（含 `&` 转义）、robots 多出 8 条 Allow、
sitemap 9 条、`/public/deployment.json` 只含 `checked_at/accounts/docs`：全部通过；typecheck/build、workspace-web 39 项、四个守卫与 Logo 校验通过；
隔离实例门户以新代码重启后 SSR 首页 `<h1>让工作，自然相连。</h1>`、`deployment.json` 返回扫描器 off/注册 open/发件人；`ui_e2e_shell.py`（Chromium 两宽度）通过。
**未验证**：真实生产的 `deployment.json`（部署后复查）。

### 16.74 Synwevo Message 独立源站（CI-66）

**盘点与决定**（2026-09-27/28）：第九轮盘点（19 步清单）确认业务层与六张表已在 `synwevo_core.messaging`、HTTP 层只依赖共享层、门户是最小依赖方模板；不免费的五件事——
没有 `messages` 角色、注册表资格模型按工作空间而消息按组织、通知目标写死 `accounts` 源、举报处理需要账户会话上的重认证、生产第四个源站由运维管理——分别按 messaging.md §6 的决定处理。
**产出**：`apps/messages/`（`synwevo_messages`、`@synwevo/messages-web`、`tests/`、`AGENTS.md`）；核心与共享层的角色、注册表、通知源、`state.messages_url`、`messagesHref`；账户服务的举报路由拆分、
`/messages` 交接与组装层挂的旧前缀兜底（注册后 410）；`all` 角色同时挂 API 与外壳；平台脚本、Dockerfile、compose、nginx、receiver、CI、守卫、npm workspace/lock、pytest testpaths 加第四个应用；
现有消息页原样搬入新外壳（只改 API 前缀、路径与页头；UI 改版另批）；协调者在 CI-68 测试中发现的缺陷一并修：读到会话末尾把该读者关于该会话的收件箱条目标为已读（`Notifier.mark_object_read`，只是收件箱已读）。
**验证**（worktree，基于 `569e1df` 全量运行；最终提交落在 `2a1043a` 之上，两者只差门户 `public.py` 的 UA 头与其测试，落上后复跑 `test_portal_public.py` + `test_messages_origin.py` + `test_role_boundaries.py` + `apps/messages/tests` 53 项通过、边界守卫通过；worktree 自建 `.venv`——共享 `.venv` 的可编辑安装指向主检出，用它跑测试会测到旧代码）：
`pytest`（`-o addopts=--import-mode=importlib`）四套件 **2154 passed, 1 skipped**（LICENSE 既有跳过）exit 0，含 `apps/messages/tests` 7 项、`services/identity/tests/test_messaging_reports.py` 2 项、
`tests/integration/test_messages_origin.py` 5 项、`test_messaging_mail.py` 1 项（CI-68 的私信邮件规则随路由搬来，末尾加读会话即标记已读）、角色/外壳/探针/工具/网关测试新增消息角色用例；
`check_boundaries`（418 Python / 188 前端文件、10 个模块边界）、`check_brand`、`check_design`、`check_component_names`、`brand_assets --check` 通过；`npm run typecheck && npm run build` 四外壳通过；
`npm test --workspaces --if-present` exit 0（UI 19、auth-client 39、Docs 49、account-web 19 项契约验证）；`python -m unittest discover -s infra/production/tests` 6 项通过。
**隔离实例**（worktree `.scratch/data`，`instance=test`，file 邮件，ports 31970/31969/31965/31968，`platform.py up --init` 四源启动、三客户端登记）：
`ui_e2e_messaging.py`（Chromium + WebKit 经本地代理）两引擎全部里程碑通过——两账户经邮件注册/邀请、经 `/auth/start` 一跳进入 `message.localhost`、私聊经事件流到达手机端并带徽标（`aria-current=page`）、
回复引用与表情回应、附件经接纳链内联渲染（`/api/messaging/attachments/...`）、建群/移出后 404 与列表消失、账户源旧路由 410、搜索按可读范围；
`ui_e2e_shell.py`（Chromium + WebKit × 1280/375）全部段落通过，门户「消息」入口 href 为 `message.localhost:31968/auth/start?next=%2F&lang=…`，点击后落在会话列表且该源 `/api/auth/me` 200、徽标由门户源回答
（截图 `docs/screenshots/shell-*-messages.png`）；手工 Playwright 检查（Chrome）：直接打开 `message.localhost:31968/` → 账户服务登录页 → 回到会话列表（标题「Synwevo Message · 消息」）；
深链 `/c/<cid>` 在只带账户源 Cookie 的新上下文中经 SSO 回到该会话；「← Synwevo」→ 门户 `/workspace` 仪表盘；「退出登录」→ 本源 `/signed-out`，消息源与账户源 `/api/auth/me` 均 401；0 CSP 违规、0 页面错误。
`platform.py down` 已停止实例。
**未验证 / 未做**：生产四源（运维项见 domain-migration.md §6：隧道主机名、compose 服务、nginx 块、`client register`、首次启动写注册表行）；`ui_e2e_moderation.py` 与 `ui_e2e_link_cards.py`
已改为驱动消息源但本轮未运行；消息页 UI 改版（另批）；事件流仍按进程扇出（账户源上处理举报删除的消息，消息源上开着的标签页在下次同步才看到）。

**协调者复核与补充（2026-09-28，main 上）**：合并为 `2f6f359`/`079c7ea` 后，共享 `.venv` 安装 `apps/messages`、`npm install`；五守卫、typecheck、四外壳 build、
`npm test --workspaces`（19+39+49+19）、infra 6 项 unittest 通过；四套件全量结果记在下方。**发现**：注册表没有消息应用时（生产在 `message.wegreen.ltd` 加入隧道之前正是如此，
且隧道由 Cloudflare 控制台管理，只能由持有人添加），账户源 `/messages` 兜底到账户外壳的「页面不存在」，而账户外壳已没有消息页——这次发布会让生产用户在过渡期读不到消息。
**补**：组装层的 `accounts` 角色在应用未注册时直接提供消息外壳（`messages-assets/` 一并挂载），外壳按本源角色把接口前缀改为 `/api/auth/messaging`，
`/c/{cid}` 与 `/messages/{cid}` 同样交接（注册后 303），开着的标签页收到 `410 moved` 时自动跳到新源；`test_messages_origin.py` 加断言（未注册：三条路径 200 且为消息外壳、账户页仍是账户外壳；注册后 `/c/{cid}` 也 303）。
**隔离实例实测**（账户服务以新代码重启、未注册消息应用）：`/messages`、`/c/cv_x` 为消息外壳、`/account` 为账户外壳、旧接口 200、`messages_url` 为 null；
`ui_check_messages_fallback.py`（Chromium 1280/390）：登录 → `/messages` 会话列表 → 新私聊选 RMember → 线程落在 `/c/cv_…` → 发送一条消息即时显示，无控制台错误
（截图 `ci66-fallback-{desktop,phone}.png`）。

**主分支四源回归（2026-09-28，协调者的隔离实例 31770/31769/31765 + 新起消息角色 31768，`messages-web` 客户端以 CLI 登记，三角色以 main 代码重启）**：
账户源 `messages_url` 变为消息源、`/messages` 303 交接；`ui_e2e_shell.py`（四源，Chromium 桌面/手机）通过，含「工作空间 消息 入口 → message.localhost:31768/ via /auth/start」；
`ui_e2e_messaging.py`（Chromium，两名真实用户两个上下文：邀请加入、私聊经流即时到达、引用回复与表情回应、图片附件扫描/去重/内联、群移除后 404 与搜索为空、0 CSP 违规 0 页面错误）通过。
四套件全量：2088 项中 1 项偶发失败（collab 变更记录计时，CI-77 已改为轮询），其余通过。

### 16.75 账户外壳文案精简（CI-76）

子代理在 worktree（`4815a4c`，基于 c48f625）完成并验证：typecheck/build、`npm test -w @synwevo/account-web` 19 项、五个守卫、`test_shells` + `test_api_platform_contract_i18n`（14 项）；
隔离四源实例（ports 32370/32369/32365/32368）Chromium 桌面+手机、WebKit：`ui_e2e_shell`、`ui_e2e_roles`、`ui_e2e_onboarding`、`ui_e2e_inbox`、`ui_e2e_push`、`ui_e2e_member_import`、
`ui_e2e_institutional_mail`（其信封层 Content-Transfer-Encoding 断言改读 text 部分——邮件自 CI-45 起为 multipart/alternative，属既有陈旧）通过，0 CSP 违规 0 页面错误；截图 10 张。
未运行：`ui_e2e_challenge.py`（需 Turnstile 测试密钥）、`ui_e2e_moderation/messaging`（消息批次另行验证）。主分支 cherry-pick 为 `a3ec6fe`：typecheck/build、`npm test --workspaces`（19+39+49+19）、
四守卫、`ui_e2e_shell.py`（四源，Chromium 两宽度）、`test_shells`/契约 i18n/`browser_onboarding_round` 通过。
### 16.76 消息服务端能力（CI-74）

子代理在 worktree（`e546756`，基于 c48f625）完成：`apps/messages/tests/test_messaging_r9.py` 17 项、`tests/integration/test_messaging_r9_composed.py` 3 项，四套件全量在其 worktree venv 运行（其最终报告待到）。
主分支 cherry-pick 为 `2fe0312`，与 CI-75 合并时手工解决 `messaging.py`（`viewer` 签名 + 逐读者卡片装饰取代原始卡片附加）、`store.py`（两批列表合并、版本 v25）、`app.py`（`start_jobs` + messaging 探针）冲突，
修正 `messaging_bridge.py` 的关键字、`test_bridge.py` 的反应响应期望（响应按反应者渲染、扇出事件不带卡片）与版本断言；合并后目标套件（`apps/messages/tests`、
`test_messaging_bridge/r9_composed/origin/mail`、`test_messaging_outbox`、`test_auth_http`、`test_messaging_reports`）全部通过；typecheck/build、`npm test --workspaces`、五守卫通过。
**隔离实例实测**（2026-09-28，三角色以合并代码重启，`api_check_r9.py`：两名真实账户经账户源登录、经 `/auth/start` 在消息源建立会话，再用该会话调 API）：
`capabilities`（recall 120 s、edit 900 s、search fts5/trigram、presence 60/300 s、voice 四类型 10 MiB）；settings 缺省 `show_presence=true, read_receipts=false`；typing 200 `expires_in_s=6`；
心跳后对方看到 `presence=online`；回执缺省 `read_by=null`，双方开启后为对方的读位置；`/unread` → `marked_unread=true, unread=1`；转发到群 201；撤回 200 且历史带 `recalled=true` 标记、他人撤回 403；
搜索 fts5/trigram 命中两会话并带 seq 与片段；`/sync` 返回 cursor/conversations/left/recalled；未知对象的对象会话 404。`/readyz` `checks.messaging` 为计数。

### 16.77 Docs ↔ Message 应用联动（CI-75）

**范围**：`synwevo_core/messaging_bridge.py`（v24：`object_conversations`、`messaging_outbox`、`message_cards`、`app_accounts`、`conversations.purpose`；对象会话规则、卡片逐读者渲染、`BridgeWorker`）、
`apps/messages/synwevo_messages/bridge_routes.py`（`/api/messaging/objects/{app}/{kind}/{id}/conversation` GET/POST、`/conversations/{cid}/object`，worker 启动）、Docs 写入点（`comments.py::create`、`jobs.py::_finalize_tx`）与核心 `project_copies.py::decide/finish`、
`messaging.py::_message_out` 的 `reader` 参数与 `sender_kind`/`card`（history/search 传读者）、`sessions.py::Sessions.create` 拒绝应用账户、Docs 项目页「更多」菜单与任务页顶栏的「在消息中讨论」（三语言）、`all` 组装层与消息应用的 `/objects` 外壳路径；记录 messaging.md §8、docs-copy.md §6。
**验证**（worktree 基于 `c48f625`，worktree 自建 `.venv` 按 production.yml 的安装步骤；测试 `-o addopts=--import-mode=importlib`）：
新增 `apps/messages/tests/test_bridge.py` 5 项（对象会话按对象授权：创建/同一会话/任务解析到项目/非空间成员 404 不泄露标题/未知应用与种类 404/个人空间 409/普通群无对象；卡片一次投递、逐读者语言、逐人扇出、计未读、无 `conversation.*` 条目、幂等（重发事件、丢失标记的重试、第二次运行是第二张卡）、反应事件不带卡片、搜索命中正文不命中标题、已删评论跳过；失去工作空间的读者先见中性卡片再被巡检移除、重加入从现在起、群主失去访问权后交接；复制申请由申请人与审批人讨论、决定与结果两张卡、动作链接指向账户源审批页与副本；应用身份无口令/无成员/不可铸造会话/不在目录/不可私聊、非法 emit 参数即拒绝），
`apps/docs/tests/test_messaging_outbox.py` 2 项（评论与回复各一行、载荷不含正文与标题、重发不重复、评论与行同事务；真实 runner 一次运行一行 `job:{jid}:1`、机器发起也写），
`tests/integration/test_messaging_bridge.py` 2 项（`all` 角色真实 Docs 路由：会话建立前的评论被标 `skipped_no_conversation` 且不建会话；回复 → 卡片（标题、摘要、`open`、链接）；真实生成任务 → `completed` 卡片与「2 份成功，0 份失败」；无 `conversation.*` 条目；bob 移出工作空间后两张卡片皆中性且无链接，巡检后 404，dan 仍读全卡；复制申请：申请人开会话、viewer 404、经账户服务审批路由（重认证）批准 → `approved` 卡片、Docs 复制执行 → `done` 卡片且副本链接在前、外发表两行、申请人收件箱只有 `copy.*`）。
邻近套件复跑通过：`test_messages_origin.py` 5、`test_messaging_mail.py` 1、`test_project_comments.py`、`test_project_copy.py` 3、`test_docs_notifications.py` 2、`apps/messages/tests` 12、`test_messaging_reports.py` 2（`test_messages_origin` 需先 `npm run build`：账户源的 303 交接路由挂在账户外壳 dist 存在时）。
全量 `pytest`（三套件，`-p no:cacheprovider`）：**2163 passed, 1 failed, 1 skipped**（650 s）；唯一失败是 `test_auth_http.py::test_bootstrap_requires_a_valid_token_and_runs_once` 的 `COUNT(*) FROM users == 0`——组装服务器启动时即创建 Synwevo Docs 的应用账户（`app_accounts`），不是失败引导留下的账户；断言改为只数非应用账户后该文件 22 项通过（skipped 是既有的 LICENSE 跳过）。
守卫：`check_boundaries`（412 Python / 188 前端文件）、`check_brand`、`check_design`、`check_component_names`、`brand_assets --check` 通过；`npm run typecheck && npm run build` 四外壳通过；`npm test --workspaces --if-present` exit 0（UI 19、auth-client 39、Docs 49 含目录三语言校验、account-web）。
**隔离实例**（worktree `.scratch/data`，`instance=test`，file 邮件，ports 32270/32269/32265/32268，`platform.py up --init` 四进程；scratchpad 脚本 `ui_e2e_bridge.py`，Chrome 153（channel）1280 群主 + 390 触摸成员，WebKit 26.6 经 `*.localhost` 代理同样两宽度）：两账户经邮件注册/邀请；群主在 Docs 源建项目、项目页「更多」→「在消息中讨论」→ 经消息源 `/auth/start` 落到 `message.localhost:32268/objects/docweave/project/<pid>`（截图 `bridge-{chrome,webkit}-docs-menu.png`、`-objects-landing.png`；外壳路由由外壳批次实现，本证明在该页面上以本源会话调用 `POST …/conversation`）→ 建群一次（群主+成员、`workspace_id`、`purpose=object`）→ `/c/<cid>` 实时；成员手机端经 SSO 进入；群主在 **Docs 源（`app` 进程）** 发评论 → **消息源（`messages` 进程）** 会话里出现卡片：POST 到 history 可见 0.23 s（两引擎），外发表 `delivered_at−created_at` 0.18 / 0.22 s、`attempts=1`、`last_error=NULL`，手机端经事件流显示「Synwevo Docs · 新评论」（现外壳按正文渲染，截图 `-card-{desktop,phone}.png`）；两账户都没有 `conversation.*` 收件箱条目；成员经账户服务 `members/{uid}/remove`（重认证 + 理由）移出工作空间后，同一张卡片对他变为中性（无标题、无链接），随后 worker 巡检把他移出会话（会话与 history 404）；两引擎 0 CSP 违规、0 页面错误。`platform.py down` 已停止实例。
**未做 / 未验证**：消息外壳的 `/objects/…` 路由与卡片渲染（契约 §8.1/8.3，外壳批次）；分享/权限变更事件（Docs 今天没有）；卡片标题改名不回写；`SCHEMA_VERSION=24` 与并行批次可能撞号；群成员 200 上限下的大工作空间（`too_many_members`，不截断）；生产未部署。

### 16.78 消息外壳重建与契约接入（CI-73）

子代理在 worktree（`9732f57`、`dee2583`，基于 d4e222a）完成并验证：typecheck/build（四外壳）、`npm test --workspaces`（UI 19、auth-client 39、Docs 49、messages-web 17、account-web 19）、
五守卫、`apps/messages/tests` 29；隔离四源实例（ports 32070/32069/32065/32068，schema v25）`ui_e2e_messaging.py` **27/27 里程碑** Chromium（Chrome）+ WebKit × 1280×800 / 390×844 触摸：
历史锚定（Δ2 px）、新消息提示、重进未读分割线、引用跳转、触摸表情回应 ≥ 44 px、IME 不误发、上传进度/取消/重试、搜索定位与回到最新、手机列表↔会话↔详情、减少动态（`--dur-2`=0 ms）、
键盘菜单、在线、回执、正在输入、撤回、仅自己删除/撤销、转发、标为未读（长按）、对象路由 404 页；0 控制台/页面错误；截图 26 张。
**协调者发现**：平台 `Permissions-Policy: microphone=()` 使所有源无法录音——改为提供消息的源（`messages`、`all`）`microphone=(self)`，`test_security_headers.py` +1（五个角色逐一断言）；
本机无头 Chrome（`channel=chrome` + fake-device 参数）`getUserMedia` 永不结算——外壳加「正在等待麦克风授权…」状态与 20 s 超时说明，`ui_e2e_messaging.py` 区分等待态与录音态。
主分支 cherry-pick 为 `d30b55f`/`4b818e8`：typecheck/build、`npm test --workspaces`（+messages-web 17）、四守卫、`test_security_headers` + `apps/messages/tests` 通过；
协调者隔离实例（消息/账户角色以合并代码重启）`ui_e2e_messaging.py` Chromium 27/27 通过（麦克风路径走等待/取消）；`--webkit` 运行两引擎共 54 条里程碑全部通过（WebKit 的麦克风被用户代理拒绝并如实报告）；四套件全量 **2186 passed, 1 skipped**，exit 0；导出树复核（typecheck/build/`npm test --workspaces` 19+39+49+17+19）与五守卫通过。
**未验证**：真实录音（需要权限提示能结算的浏览器/真机）、真机 iOS/Android。

### 16.79 账户表单的未保存保护（CI-79）

**复现**（2026-09-28，代码）：账户外壳没有任何 `beforeunload`/未保存检查；账户栏的「返回工作空间」、上一级与应用链接都是整页导航，个人资料等表单的输入在离开时直接丢失。
**修复**：见 issues.md CI-79。**验证**：account-web typecheck、19 项契约验证、build；`ui_e2e_shell.py`（四源，Chromium + WebKit，桌面）新段通过：真实点击与键入（WebKit 只在真实手势后询问）后处理器就绪；
Chromium 点「返回工作空间」→ 浏览器询问（`beforeunload`）→ 留下，输入仍在；改回原值 → 处理器解除 → 直接离开且没有第二次询问。WebKit 的 Playwright 自动化不向脚本展示该询问（连 `page.close(run_before_unload=True)` 也不结算），
改为派发 `beforeunload` 事件核对 `defaultPrevented`（编辑时 true，改回 false）。**未验证**：真机 Safari/手机浏览器上的询问外观。

### 16.80 第十轮五主机：站点与工作空间分主机（CI-82）

**复现**（2026-09-28，线上只读）：`https://workplace.synwevo.com/` → 308 `https://synwevo.com/workspace`（第九轮的别名规则，与第十轮映射相反）；
`https://message.synwevo.com/` → 502（请求已到网关，网关对未配置主机答 444）；`https://message.wegreen.ltd` 无法连接。
**实现**：见 issues.md CI-82。**验证**：`tests/integration/test_portal_public.py` 新增三项（站点主机：公开页 SSR、canonical/og 用站点、`/workspace`、带查询的 `/workspace?ws=`、`/auth/start`、`/signed-out` 308 到工作空间主机、`/auth/callback` 不移、伪造 `X-Forwarded-Host` 不影响目标、sitemap 全是站点地址、`deployment.json` 留在站点；
工作空间主机：根即工作空间外壳（`synwevo-surface`，无 h1/canonical/og，noindex）、`/privacy` `/en` `/zh-Hant/service` 308 回站点、robots 全禁、两主机公开状态 `portal_url`/`site_url` 一致；单主机：不设站点地址时什么都不移）与既有用例全部通过；
`test_shells`、`test_sso_chain`、`test_messages_origin`、`test_security_headers`、`test_oidc_flow`、四守卫、typecheck/build、`npm test --workspaces`、infra 6 项通过；`nginx -t` 通过。
隔离实例门户以 `SYNWEVO_SITE_URL=http://site.localhost:31769` 重启（同一进程两主机），`ui_check_split.py`（Chromium 1280/390）：站点首页「登录」→ 账户登录页 → 落在工作空间主机 `/workspace`；工作空间主机根即工作空间并有「消息」入口；
品牌链接与页脚「数据与隐私」指向站点；账户页「返回工作空间」→ 工作空间主机；0 页面错误。之后门户恢复单主机，供其它证明使用。

### 16.81 消息完整性（CI-78）与导出/交接（CI-81）

**CI-78**（子代理 worktree `8244d27`，基于 4fc58e5；主分支 cherry-pick `290449c` 无冲突）：`apps/messages/tests` 37、`tests/integration` 636、`services/identity/tests` 634、`apps/docs/tests` 891 通过 1 跳过；
五守卫、typecheck/build、`npm test --workspaces`（19+39+49+20）通过；隔离四源实例 `ui_e2e_messaging.py` 33/33 里程碑 Chromium（Chrome）+ WebKit × 1280×800 / 390×844（新增：提及 @ 出现与清除、第二个组织的筛选、群头像对另一成员可见、
屏蔽双向拒绝且措辞正确 → 解除后可发、私聊转群预选与「之前的私聊记录不会带入新群」），0 控制台/CSP/页面错误；截图 42 张。已知：CI-73 的语音步骤在 Chromium 四次中失败一次（录音上传后未在等待内发出，后三次通过，原因未查明）。
**CI-81**（协调者，main 上）：`services/identity/tests/test_messaging_departure.py` 4 项通过（导出 `synwevo-account-2` 只含本人消息与设置、他人正文不出现、私聊不带对方名字；到期删除时群交给最早管理员并写交接/离开系统行、空群归档、私聊对方保留历史、二次清扫不变；
既往已删除账户被清扫；离开组织交接给最早加入的成员）；隐私页与账户页三语言改写，`npm test --workspaces` 与 typecheck 通过。四套件全量结果记在本节末尾。

### 16.82 消息应用的拒绝文案（CI-83）

复现（隔离四源实例，夹具账户）：个人工作空间里的项目在 Docs 菜单里有「在消息中讨论」，点开后消息源对象页只显示「请求未完成（no_organization）」和「重试」；扫描消息服务 80 句 `ServiceError` 原句，外壳目录一句都没有（CI-78 的 6 句除外）。
修复后：消息外壳 `npm test` 25 项通过（新增 `refusals.test.ts` 5 项：服务端每句拒绝在目录/模板/「仅 API」三类之一；删掉「群主退出前…」一条译文后测试失败且英文呈现为 “The request could not be completed (owner_must_transfer).”，恢复后通过；
模板三语言断言；`mayRetry`）。`apps/messages/tests/test_refusal_details.py` 2 项通过。typecheck、build 通过。浏览器（Chrome，1280×860 与 390×860）：同一夹具账户经账户服务自己的 `POST /api/auth/onboarding/personal` 得到个人空间，
个人空间项目菜单 9 项、无「在消息中讨论」，组织空间项目有；对象页中文显示「个人工作空间的项目没有组织会话；复制到组织工作空间后才能讨论」、「返回工作空间」、无「重试」，切到 English 后显示对应英文句子与 “Back to the workspace”，
0 页面错误；截图 `ci83-docs-personal-*`、`ci83-object-personal-{zh,en}-*`（scratchpad，未入库）。

### 16.83 脱敏缩放不再原样复制金额（CI-84）

CI-83 的全量回归（2214 通过、1 失败、5 跳过）里 `apps/docs/tests/test_regression_cases.py::test_desensitize_copies` 失败：脱敏后 D2 金额仍是 1123.45。单独重跑 8 次均通过——偶发；
读 `engine/desensitize.py`：比例 `round(uniform(0.6, 1.4), 3)` 取到 1.000 的概率约 1/800。修复后 `test_engine_desensitize.py`（含新增项）与 `test_regression_cases.py` 通过。

### 16.84 账户外壳的拒绝文案（CI-85）

复现（代码审读）：账户页 `errorText` 对 29 个代码以外的拒绝一律显示「操作失败，请重试。」；管理/成员页 `errText` 与批量逐项只按代码，其余「操作未完成，请刷新后重试。（code）」；API 控制台总是「操作失败，请检查输入或重试。」加代码；导入逐行失败与身份提供商探测结果把中文原句原样给所有语言；平台管理员重新验证时任何 401 都是「口令不正确」。
修复后：`services/identity/web` 的 `npm test` = `flow.validation.mjs` 19 项 + `tests/refusals.test.mjs` 7 项，全部通过。守卫逐页（登录、账户、组织、平台管理、成员、API 控制台，各自一套 vite 模块图：入口 chunk + 该页 chunk）用该页的呈现函数把每句可达拒绝译成英文，
要求是译文、非通用兜底、非仅共享代码标签；删掉 `account.messages.ts` 里「找不到该会话」一条后第三项失败，报 “account ← auth_routes.py build_router.revoke_session no_session: 找不到该会话 — generic: The request could not be completed (no_session).”，恢复后通过。
服务端：`services/identity/tests/test_refusal_details.py` 4 项（口令规则与数字、MFA 剩余次数、安全密钥/设备/导入文件/自定义角色上限的 `detail`、外部登录起点回到登录页），`test_members.py`（邀请日上限 `detail.limit`、批量逐项只带数字、「退出工作空间」原句）、`test_identity_provider_status.py`（探测结果带提供商与故障）、`test_external_identity.py`（未启用提供商的起点 303 到 `/login?ext_error=unknown_provider`）。
`services/identity/tests` + `tests/integration`：1268 通过、13 跳过（工作树里没有被忽略的 `testdata/`，`test_org_templates.py` 1 项先因缺夹具文件失败，接上主检出的 `testdata` 后该文件 2 项通过）。
`check_boundaries`、`check_brand`、`check_design`、`check_component_names` 通过；`npm run typecheck`、`npm run build` 通过；`npm test --workspaces --if-present`：ui 19、auth-client 39、docs-web 49、messages-web 25、account-web 19 + 7，全部通过。
构建后账户入口 chunk 313.16 kB（gzip 97.11 kB），入口共享 chunk 112.86 kB（gzip 40.83 kB）。浏览器未复核（本轮只在代码与测试层面验证）。
### 16.85 Docs 工作台的拒绝文案（CI-86）

基线（读代码，逐个呈现路径核对）：`issueText` 对不在 `ERROR_SOURCE` 的代码直接用共享 `apiFailureText`，它只认目录里已有的原句，于是 `overlap`、`invalid_field`、`bad_selection`、`already_published`、`copy_already_requested`、`workspace_unavailable` 等在三种语言里都是「请求未完成（code）」；
`ERROR_SOURCE` 里的代码（`bad_value`、`bad_rule`、`bad_naming`…）先于原句，「该列不存在」也变成「请求参数无效，请检查设置。」；`bad_format` 被说成文件格式不支持；评论抽屉直接显示 `ApiError.message`；文档编辑器的实时协同关闭原因、复制申请失败原因直接显示代码；
`copy.failed` 通知正文在英文与繁体里也是中文句子。按构造器扫描 `apps/docs/synwevo_docs` 与 `synwevo_core/project_copies.py`：587 处拒绝（`ServiceError` 364、`not_found` 18、编辑器契约 `_bad` 23、路由转交的格式引擎错误其余）。
修复后（工作树 PYTHONPATH 指向本工作树的包；共享 venv 的可编辑安装指向主检出，不这样做测到的是旧代码）：
Docs 外壳 `npm test` 59 项通过，其中新增 `tests/refusals.test.ts` 10 项：服务端每句拒绝须在目录（189 条）、有模板（18 个代码）、有句形（78 条）、在逐句登记的代码映射或带理由的「仅 API」清单（87 句 + 6 个仅机器接口的模块）；删掉「该列不存在」一条译文后该项与「具体原因」一项失败、恢复后通过（变异验证）；
句形在三种语言里都能读回自身、英文不含中文、zh-Hans 等于原句；模板三语言逐句断言（经 `errorText` 与 `ApiError.message` 两条路径）；网关的工作空间/应用拒绝与 PDF 引擎常量可呈现。`apps/docs/tests/test_refusal_details.py` 9 项通过（上传上限、模板占用、模板格式、导出格式/样本上限/解析中、OCR 未指定页码的建议、正则错误、评论与编辑器上限、复制失败原因三语言、行数上限整句）。
`pytest apps/docs/tests -q -p no:warnings` 全量 901 通过、1 跳过（LICENSE 未选定）、0 失败（前端已构建，缓存头 8 项随之运行，未构建时它们跳过）；`tests/integration/test_project_copy.py`、`test_gate_api_errors.py`、`test_api_platform_contract_i18n.py` 17 项通过；typecheck、build、`npm test --workspaces --if-present`（ui 19、auth-client 39、docs-web 59、messages-web 25、account-web 19 项校验）、四个检查脚本与 `brand_assets.py --check` 通过。
未做：浏览器实测（本轮未起隔离实例）；来源文件解析状态里的引擎原句（`diagnosticText`，非拒绝）仍有未编目的，按句形与目录逐步补。

### 16.86 公开页的消息事实（CI-88）

复现：`apps/portal/web/src/PublicHome.tsx` 与 `PublicPages.tsx` 三处写「没有在线状态、他人已读回执或正在输入提示」，与 `messaging_settings` 缺省（`show_presence=1`、`read_receipts=0`，互惠）及 CI-73/74 的界面不符；首页消息卡链到账户源站 `/messages` 并说「正在成为独立的应用」。
修复后：portal typecheck、build 通过；隔离实例 Chrome 1280×900 / 390×900 × 简体 / 繁體 / English：首页与 `/privacy` 各出现新句，页面中不再有旧句；已登录夹具账户在首页点消息卡，链接为 `http://message.localhost:31768/auth/start?next=%2F&lang=zh-Hans`，落在消息应用；0 页面错误（`scratchpad/ci88_proof.py`，截图 `ci88-home-anon-*`）。

### 16.87 网关按请求解析上游（CI-89）

本机（Docker 28.3，`nginx:1.28-alpine`，一次性网络 `ci89net`，`scratchpad/ci89_gateway_proof.sh`）：四个替身上游按角色名注册；新 `nginx.conf` 的网关对 docwave 答 `docs`；删除并按「先 messages 后 docs」重建，两者地址互换（docs 172.18.0.2→.3），重建后立即一次请求仍落到旧地址（缓存窗口），11 s 后答 `docs`、message 主机答 `messages`，网关未重启；删除 docs 后 docwave 答 502、accounts 照常；`nginx -t` 通过。
生产（隧道）：五主机 `/api/auth/state` 的 `role` 各自正确；旧主机 308 不变；`/api/messaging/stream` 匿名 401 JSON（messages）；`/api/collab/…` 普通 GET 401 JSON（docs）、握手 403（应用在 accept 前拒绝匿名）。
### 16.88 历史文件补扫与隔离区（CI-90）

基线（读代码）：扫描上线前的行 `scan.status=not_configured`/无记录，照常可读；file-acceptance §8 的补扫只有 SQL 路径。补扫若只移走原字节，
已解析来源的 `working_blob`/`parsed_blob` 仍经视图、搜索与 `/api/preview-files/{blob}` 可读；模板没有被拒绝的状态；`blob_audit` 把被拒绝行引用的
摘要算作缺失，`snapshot.py restore` 因此拒绝整份备份；`member_import_routes._save_upload` 的原文件在导入未执行时一直保留（`sweep_expired` 无调用方）。
修复后（工作树 PYTHONPATH 指向本工作树的包；共享 venv 的可编辑安装指向主检出，不这样做测到的是旧代码）：
新增 `apps/docs/tests/test_rescan.py` 13 项、`apps/messages/tests/test_attachment_rescan.py` 5 项、`tests/integration/test_rescan_shared_bytes.py` 1 项，全部通过
（clamd 协议替身：EICAR、按摘要标记、ERROR、离线、流上限、签名撤回；断言见 file-acceptance §7.3）。
`pytest apps/docs/tests apps/messages/tests tests/integration -q -p no:warnings`：1598 通过、1 跳过、0 失败（Docs 914 通过、1 跳过——LICENSE 未选定；消息 44；集成 640；前端已构建，缓存头与外壳测试随之运行。账户服务套件因共享的 messaging 改动另跑：642 通过）。
typecheck、build、`npm test --workspaces --if-present`（ui 19、auth-client 39、docs-web 59、messages-web 25、account-web 19 项校验与 7 项测试）、`check_boundaries`/`check_brand`/`check_design`/`check_component_names` 通过。
真实入口演示（`scratchpad/ci90_demo.py`，一次性数据目录 + 协议替身）：`rescan --dry-run` 退出 0（「来源：2 份内容（2 行）…扫描器：可用」）；`rescan` 退出 1，
逐行只有 id、摘要前缀与签名，摘要给出完整摘要、行 id 与「1 行没有上传者记录」；再次运行「没有需要补扫的内容」退出 0；`quarantine list` 列出摘要、状态、签名、
仍拒绝的行数；替身离线时退出 3 且什么都没改。
未做（初版时）：真实 ClamAV 引擎上的补扫；生产运行；浏览器里查看被拒绝模板/附件的提示。

**三轮独立审查与修复（2026-09-28，同一工作树分支，合并前）。**
第一轮（只读审查初版）：1 高——用后来被拒的模板/来源生成的产物、打包下载、机器接口链接与预览授权仍可读；9 中、6 低（`purge` 整个删掉
回归用例、隔离区不进备份、Docs 不认另一应用的结论、守卫以"字节不在存储"为准而字节可能回来、恢复只重扫一份内容就记模板干净、
组织共享模板发布从未补扫、崩溃后不自愈、恢复标记未恢复的行且不取锁、`blob_audit` 的豁免过宽、在途扫描覆盖补扫结论、超限文件卡住
`--limit`、默认就有副作用、隔离副本不核对摘要、退出码）。第二轮（复审修复）：2 高——一个应用的补扫与另一个应用的恢复并行时刚恢复的
文件被删；另一应用的结论下已记干净的行永不被标记、整个项目 410——以及 4 中（脱敏授权撤得过宽、`--limit` 被缺失文件卡住、结论集合
缓存在粗粒度时钟上会过期、产物下载每次解码整份快照）。第三轮：1 高——同一文件在扫描上线后再次上传被判恶意时，之前那条来源的字节
随之删除；1 中（`--limit` 不数失败的扫描）；若干低。每条发现都有回归测试（`test_rescan.py` 35 项、`test_attachment_rescan.py`
8 项、`test_rescan_shared_bytes.py` 3 项，全部通过；审查者的探针脚本在 `scratchpad/review/`）。第四轮确认四处修复成立、无新的产品缺陷，
指出一项测试的等待条件（等状态而非等隔离）会偶发失败——已改为等隔离记录，连续三次通过。

**真实引擎（本机 Homebrew ClamAV 1.5.4，库 28137，`scratchpad/ci90_real_engine2.py`，一次性数据目录）。** 扫描关闭时存入：工作簿、
标准 EICAR 测试文件（整份 69 字节）与模板，并用这个项目生成一个任务（快照含两份来源）。开扫描后 `python -m synwevo_docs.cli rescan`
（预览，退出 0：来源 2 份 3 行、模板 1 个 2 份内容、扫描器可用）→ `rescan --apply`（退出 1：工作簿与模板干净，EICAR 感染、移入隔离区、
摘要列出用过它的任务 id）；之后 EICAR 来源下载 410、任务产物 410 `rejected_malware`、模板 200；`quarantine release` 退出 1（当前引擎仍判
`Eicar-Signature`，拒绝恢复）；`purge` 不加 `--yes` 退出 2、加了退出 0；`quarantine list` 显示 `purged`；再运行两个应用的 `rescan --apply`
都是"没有需要补扫的内容"、退出 0。另见：EICAR 后面跟着其他数据的文件真实引擎不报（EICAR 签名要求整份文件），测试样本因此用整份 EICAR。

**全量（工作树包路径）。** 在 4a7531c 上四个套件合计 2281 通过、1 跳过（LICENSE 未选定）、0 失败；typecheck、build、
`npm test --workspaces --if-present`（ui 19、auth-client 40、docs-web 59、messages-web 25、account-web 7）、
`infra/production/tests` 6 项、`check_boundaries`/`check_brand`/`check_design`/`check_component_names`/`brand_assets --check` 通过。
最终修复提交（c98fe03，压成一个提交前）上 Docs、消息与集成三个套件 1634 通过、1 跳过、0 失败（账户服务套件在 4a7531c 上通过，其后的改动不涉及它）。

### 16.89 演示文稿明细的超出处理：保持版面时该份失败、复制本张幻灯片续写（CI-119、CI-120）

基线（读代码）：`pptx_fill` 在明细表格超出幻灯片底部时只给 `slide_overflow` 警告；`fill_checked` 的页数守卫对 PPTX
数的是幻灯片数，表格变长不增加幻灯片，所以 layout=fit（「保持模板页数：仍超出则该份失败」）对 PPTX 从不失败。
PDF 明细区域早有「超出时：视为错误 / 复制本页续写 / 压缩行高」，PPTX 没有任何选择。

修复后：`test_engine_documents.py` +5（fit 下为错误、off/warn 为警告；续写 40 行 → 5 张幻灯片 11/11/11/7 行明细、表头每张、
合计只在最后一张、标题字段每张都是「客户：乙公司」、备注只留在原幻灯片、续写的表格都在幻灯片内、渲染器读到 5 张；
能放下时不加幻灯片；error/warn 两种设置；含图表的幻灯片不复制；分块算法 7 例）；`test_slot_options.py` +1（PPTX
只接受 warn/error/continue，拒绝 shrink；DOCX 仍拒绝 warn）；含 PPTX/字段的 Docs 测试全部通过；`refusals.test.ts` 16 项
（新句子与模板在三语言逐样本链路里）；docs-web 66 项；typecheck 通过。LibreOffice 把续写结果渲染为 5 页，第 1 页 11 行
明细到底边、第 4 页 7 行 + 合计（`scratchpad/pptxcont/slide1.png`、`slide4.png`）。

浏览器（隔离实例 31765/31770，Chrome 1400，`scratchpad/pptx_cont_proof.py`，新注册测试账户与测试夹具）：缺省设置下预览给出
「……可在字段设置中把「表格超出幻灯片时」改为「复制本张幻灯片续写」……」警告；字段面板里该选择框三项（照常生成并提示 /
视为错误（该样本不生成）/ 复制本张幻灯片续写）并显示说明，选择后服务器存为 `continue`，预览不再警告；生成任务完成、产物
「已校验」，下载的演示文稿 5 张幻灯片（表格行数 12/12/12/9，第 5 张「结束」）；英文界面同一选择框与说明为英文；0 页面错误
（`scratchpad/pptxcont/shots/1-setting-zh.png`、`2-job-zh.png`、`3-setting-en.png`）。首版选项文字在窄选择框里被截断，
已把「幻灯片上其他字段同样填写」移入说明。

未验证：PowerPoint 实机打开续写结果（只用 LibreOffice 与 python-pptx 读回）；按模板行高计算，文字换行使行变高时仍可能超出；
文本框文字溢出不检测。

### 16.90 表格实时房间 B1 ② 合并与文档房间的并发修复（CI-121）

五轮独立审查（第一轮 10 项、第二轮 7 项与 Q19、第三轮死锁（高）等 7 项、第四轮墓碑丢编辑（高）等 5 项、第五轮 5 项，
每项有在前一提交上失败的回归测试；复现脚本在 `scratchpad/b2review3/`、`b2review4/repro/`、`b2review5/repro/`）。
合并前主代理核对：第五轮修复的文档房间路径与新关闭码 4503（y-websocket 只对 4400–4499 停止重连，4503 按退避重试）；
重跑全部复现脚本——依赖已删除内部接口的（墓碑行、`hub.background`、`_closing` 的旧形状）按接口失败，与删除竞争的加入
现以 4410 关闭（旧复现断言握手完成），丢弃编辑的复现因夹具自身死锁（`row()` 等待关闭写、关闭写等待被测试线程放行的折叠）
失败，改为由计时线程放行的变体（`scratchpad/b2verify/test_discard_variant.py`）通过：丢弃的编辑不再回来、草稿在 v2 且干净。

合并后 main（ded6202）上的预推送：守卫五项、typecheck、build、`npm test --workspaces`（19/40/66/25/7）、infra、pytest
2545 通过、1 跳过、0 失败。隔离实例（Docs 服务在合并后的 main 上重启）四个协同浏览器证明全部通过：`ui_e2e_collab.py`
（桌面与手机两人互见输入与光标、变更记录、v4→v5 连续保存、改为查看者后连接只读、服务端不应答时单人编辑）、
`ui_e2e_restore_live.py`（恢复前点名未保存的实时修改、确认后恢复、有未保存修改时撤销被拒、裸恢复 409）、
`ui_e2e_sheet_collab.py`（表格编辑器在场与按单元格合并、同格冲突）、`ui_e2e_project_collab.py`（项目页在场、合并、冲突拒绝）。

未验证：表格实时模式本身的浏览器证明（开关关闭，属 ⑤）；多实例（单主租约属 B2）；生产（部署受阻）。

### 16.91 XLSX 重复内容块与明细行修复（CI-122、CI-123）

设计 `docs/platform/docs-xlsx-nested.md`（设计代理起草、主代理审阅答复 6 个待定问题后实现）。三轮独立审查：第一轮 600 个随机
单独明细模板主干对照（除末行夹紧外逐字节相同）、612 个随机块配置结构与取值、LibreOffice 60 份一致，发现 2 中若干低与一个生产既有
缺陷（明细行 `SUM($B$5:B5)` 展开错）；第二轮发现修复过头（绝对终点区域回归，高）等 8 项；第三轮 800 个随机模板 35 种明细行公式形状
主干对照逐项分类，全部差异属预期修复类别，绝对终点形状与主干零差异，LibreOffice 抽样 577 个公式单元格一致，块内 800 种子取值中 7 处
与期望不同均为文档化的「上一行向下填充」规则。脚本与夹具在 `scratchpad/xlsxreview/`（round2、round3）。

合并后 main（08e92c9）：`refusals.test.ts` 16/16；浏览器证明 `scratchpad/xlsx_band_proof.py`（`_testenv` 一次性实例，无登录）在
合并后的 main 上重跑通过：整行选择建块、行 5 作明细行（父为块）、B3/D3 作块的列；未设分段前预览给出整句说明；设「订单号」后预览
「已校验 8 个值」；生成的 A.xlsx 第 3–7 行 A-1（D7=SUM(D5:D6)）、8–11 行 A-2、D13=SUM(D7,D11)=18.60，校验 8 个值无缺失；英文对话框
无残留中文、繁体侧栏说明、手机宽度对话框 382/390 px、0 页面错误（`scratchpad/xb/shots-merged/`）。

预推送（08e92c9）：守卫五项、typecheck、build、`npm test --workspaces`（19/40/66/25/7）、infra 通过；pytest 2612 通过、1 跳过、**1 失败**：
`tests/integration/test_project_copy.py::test_a_changed_source_withdrawal_and_self_approval`（自己批准的复制在执行时判为 stale）。
单独重跑 3 次、负载下 12 次、集成套件单独 658 通过均未复现；该测试在上一次合并的全量运行中通过，两次合并都未改复制代码；已交独立代理
查根因（进行中）。

### 16.92 表格实时协同 B1 ③④⑤（浏览器同步、编辑器、两账户证明）

③ `sheetmodel.ts`/`sheetsync.ts`：与 `collab_sheet.py` 同一套操作语义，约 1 300 步的共享语料由 Python 生成并检入，
`sheetmodel.test.ts` 逐步重放，`test_sheet_live_corpus.py` 保证语料与 Python 模型一致；④ `SheetEditor` 在房间说「实时」时用同步视图，
否则仍是 CI-47 模式；⑤ `services/identity/scripts/ui_e2e_sheet_live.py`。

证明（隔离实例 31765/31770，`scratchpad/sheet_live_run.py` 代管该实例的 Docs 进程：数据目录必须是 scratch 路径，重启只动这个进程——
脚本自带的 `platform.py restart docs` 在未设 `SYNWEVO_DATA_DIR` 时会落到仓库的 `data/`，因此不用）：`SYNWEVO_SHEET_LIVE=1`，
两个真实账户（桌面 + 390 px 手机），Chrome 与 WebKit 全部通过：互见选区与名字、双向实时修改、同格冲突「用我的值」、离线加 10 行
回来恰好一次（9 → 19 行）、手机输入期间重启 Docs 进程（0.7 s）后两边回到同一房间且未丢/未重、区域字段与合并冲突以句子拒绝、
撤销跳过对方之后改过的单元格并说明、保存为 v2 且内容正确、繁体与英文界面无残留、390 px 无横向滚动
（截图 `scratchpad/sheetlive-shots/`）。开关关闭时同一代码上 `ui_e2e_sheet_collab.py`、`ui_e2e_collab.py` 通过（现有模式不变）。
合并后预推送 2616 通过、1 跳过；docs-web 111 项。

未验证：同步引擎的独立审查（进行中，是打开开关的门槛）；变更记录与查看者专项（B2）；生产。

### 16.93 表格实时协同同步审查的修复（H1–H10 与页面项）

同步引擎独立审查（约 1 300 次真实 SheetSync 对真实房间的随机交错，收敛可靠）列出语义问题：撤销删除他人改过的新工作表（高）、忙/限流后乱序、
「用我的值」覆盖未告知的单元格、崩溃后已确认编辑消失、崩溃后排队编辑绕过冲突、未答复时撤销错对象、合并撤销顺序等及九项页面问题。实现者逐项修复
（06eb332、924103b、2826d3b），新增 `apps/docs/tests/test_sheet_live_clients.py`（真实 SheetSync 标签页对真实房间，15 项定向 + 5 种随机 × 12 种子，
检查收敛、不变量、追加不重复、不静默丢失、每个标签页按发送顺序生效、撤销不删他人工作表；CI 以 `SYNWEVO_SHEET_CLIENTS=required` 运行）。
协议新增：`detail.held`（拒绝 n 后按序）、`detail.also`（冲突列出全部单元格）、hello 的 `loaded {id, seq}`（崩溃重载后标签页重发已确认但未持久化的编辑）。

证明（隔离实例，`scratchpad/docsctl.py` 只管理 scratch 实例的 Docs 进程，经 `SYNWEVO_E2E_DOCS_CTL`）：`SYNWEVO_SHEET_LIVE=1` 两个真实账户、Chrome
与 WebKit 全部通过——静止时两页每个单元格等于房间状态、确定性的断线期间输入、确认后立即 SIGKILL（无关闭写入）重启后 D2/D3/E2/E3 都在且无人被询问、
删除手机正在输入的工作表后输入不被写到别处并可从「已删除的工作表」恢复、繁体无简体专用字（282 个）；开关关闭时 `ui_e2e_sheet_collab`、`ui_e2e_collab`、
`ui_e2e_restore_live` 通过。合并后预推送（4678a44）2696 通过、1 跳过。

未验证：复审结论（进行中，决定是否试开开关）；生产。

### 16.94 有密码保护的上传（CI-125）

本机真实 ClamAV 1.5.4（`AlertEncrypted yes`，127.0.0.1:33101）对夹具：加密 ZIP → `Heuristics.Encrypted.Zip`；需要口令与仅所有者口令的 PDF 都 →
`Heuristics.Encrypted.PDF`（后者可无口令打开，若采用会被当成恶意）；普通文件 OK——因此生产只开 `AlertEncryptedArchive`，其余由核心检测器判定。
实现与三轮独立安全审查见 issues.md CI-125（样本与脚本在 scratchpad `encsamples/`、`encreview/`；第三轮的绕过样本已删除，不入库）。

浏览器（隔离实例，消息服务在合并后的 main 上以 `SYNWEVO_CLAMD_ADDRESS=tcp://127.0.0.1:33101`、`SYNWEVO_MALWARE_SCAN=required` 重启，
`scratchpad/encrypted_attach_proof.py`，新注册测试账户、只含自己的群）：`zip -P` 的 locked.zip 在编辑框里被拒，简体「文件设有密码或含有加密内容……请移除密码
（或另存一份不加密的副本）后重新上传」、英文「The file is password-protected or contains encrypted content……」、繁体同义，均无代码、无「重试」；
不加密的 open.zip 正常上传待发送；0 页面错误。`ui_e2e_messaging.py` 全程通过（503 的暂时拒绝仍提供重试并在重试后成功）。
合并后预推送 2747 通过、2 跳过（其一为需要 `CLAMD_TEST_ADDRESS` 的真实引擎测试）。

未验证：生产主机上的 clamd 配置挂载（随下次部署执行）；Docs 来源/成员导入的浏览器核对（节点测试与接口测试覆盖）。

### 16.4 真实外部邮箱与真实业务样本（接续项）

| 项 | 本轮完成 | 需要真实条件 |
|---|---|---|
| 外部邮箱 | 验证码/激活/安全通知模板三语言、file 投递、outbox 脱敏、限流与单次消费均有测试；生产 SMTP 沿用上一轮验收 | 授权收件箱实际收到验证码并完成登录：需要用户指定一个可用的测试地址 |
| 业务文档 | 合成样本主链路桌面与手机各一次读回；格式矩阵、回归用例工具沿用 | 用户拥有使用权的真实模板与来源（脱敏副本）；最小清单：1 份 DOCX/XLSX/PDF 模板 + 对应 100 行以内来源 + 期望结果 2 份 |
| Passkey | 隔离环境软件认证器回归沿用；RP ID 未改 | 本人在手机/电脑平台认证器或硬件钥匙上注册并再次登录 |

## 15. 发布判断

* 本轮范围内**已确认缺陷为 0**：`docs/quality/issues.md` 中 DW-001…DW-012、DW-030…DW-032、DW-040…DW-051 全部为「已修复」并附验证证据。
* 约定检查全部执行并通过：202 项后端测试、3 套浏览器端到端、类型检查、生产构建、干净环境启动、无界面调用、界面/接口一致、权限与输入、幂等并发、取消语义、Webhook 全链路、持续使用资源。
* 仍然存在的限制与未测量项见第 14 节与 `README.md`「已知限制」，**不计入已交付**。
* 结论：可以发布到自托管环境。对外提供服务前需按 `docs/DEPLOY.md` 配置反向代理与界面鉴权，并确认 PyMuPDF 的许可证义务。

---

# 平台单仓库重组轮（2026-09-24）

环境：macOS（Apple Silicon），Python 3.14.5（仓库根目录 `.venv`），Node 22 / npm workspaces。
数据目录在各条命令里显式给出；**真实业务库全程未被任何服务打开**，涉及运行的验证都指向
`/tmp/platform-live`（隔离实例）或真实库的只读副本。

## 实际执行的命令与结果

```bash
# 迁移前基线（改名之前，在 .../Synwevo Docs 里）
backend/.venv/bin/python -m pytest backend/tests -q
# → 644 passed, 1 skipped in 186.36s

# 重组之后（仓库根目录）
.venv/bin/python -m pytest
# → 650 passed, 1 skipped in 158.49s

# 模块单跑
cd apps/docs && ../../.venv/bin/python -m pytest tests
# → 306 passed, 1 skipped in 104.53s

# 模块依赖边界
.venv/bin/python scripts/check_boundaries.py
# → 检查了 156 个文件，覆盖 7 个模块边界。
#   没有跨应用导入、没有 sys.path 改写、没有机器绝对路径、没有指向旧目录的链接。

# 前端
npm run typecheck && npm run build
# → 三个外壳（@synwevo/docs-web、@synwevo/account-web、@synwevo/workspace-web）全部通过

# 统一登录链路（对着重组后真正跑起来的三个进程）
SYNWEVO_DATA_DIR=/tmp/platform-live .venv/bin/python tests/integration/verify_sso.py \
    --email owner@example.test --password '***' --docweave http://docweave.localhost:8765
# → 38/38 项通过

# 真实库的隔离副本上跑迁移
.venv/bin/python tests/integration/migration_rehearsal.py --from data
# → 15/15 项通过；来源只读；default 仍 legacy/pending_claim/0 成员

# 一致性备份与恢复演练
SYNWEVO_DATA_DIR=/tmp/platform-live .venv/bin/python scripts/platform.py backup --out /tmp/platform-live-backups
.venv/bin/python scripts/platform.py verify-restore /tmp/platform-live-backups/docweave-2026-09-24T00-14.tar.gz
# → 20/20 项通过。"这份备份是可恢复的。"
#   备份同时报出：主库 0.5 MB / WAL 2.7 MB —— 只复制主库会丢掉 2.7 MB

# 干净检出（等效于另一个人第一次拿到仓库）
git clone --no-hardlinks <repo> /tmp/clean-checkout && cd /tmp/clean-checkout
python3 scripts/platform.py setup        # → EXIT=0，装好依赖并构建三个外壳
.venv/bin/python -m pytest               # → 见下

# 数据目录解析（三个起始目录，同一个库）
.venv/bin/python -m synwevo_docs.cli info                                   # 平台根目录
(cd apps/docs && ../../.venv/bin/python -m synwevo_docs.cli info)       # 应用目录
(cd services/identity && ../../.venv/bin/python -m synwevo_identity.cli info)
# → 三者都报告 /Users/lihiko/repo/wegreen/synwevo/data

# 空数据目录守卫
SYNWEVO_DATA_DIR=/tmp/does-not-exist .venv/bin/python scripts/platform.py up
# → 退出码 2，并说明「这里不会替你建一个空库」
```

## 真实数据完整性（迁移前后逐项比对）

| 检查 | 结果 |
|---|---|
| `pragma integrity_check` | ok |
| 44 张表逐表行数 vs 迁移前备份清单 | **完全一致**，无一处差异 |
| 69 个 project id 逐一比对 | 完全相同 |
| 全部项目的 tenant | 仍然只有 `default`（69 个） |
| `default` 工作空间 | `legacy / pending_claim / 0 成员`，未认领、未重置 |
| 695 个 blob 的 SHA-256 清单 | 与迁移前**逐字节一致**；每个文件的内容摘要等于其文件名 |
| `data/` 的位置 | 随仓库根目录改名一起移动，这是本轮唯一一次数据位置变化，有完整备份与上述核验 |

## 机器自动化（无浏览器、无 Cookie）

```bash
.venv/bin/python -m synwevo_identity.cli --data /tmp/platform-live client register \
    --client-id nightly-batch --app docweave --name 夜间批处理 \
    --service --workspace w_xxx --api-scopes "docweave.read docweave.write"

.venv/bin/python tests/integration/automation_example.py \
    --base-url http://docweave.localhost:8765 --issuer http://accounts.localhost:8770 \
    --client-id nightly-batch --client-secret '***' \
    --source testdata/测试_客户100.xlsx --template testdata/测试_合同模板.docx \
    --definition docs/integration/example-definition.json --out /tmp/automation-out
# → 任务 completed，样本 10，成功 10，输出 10 个 .docx（各约 39.5 KB）
```

打开产物逐条核对（不是只断言 HTTP 200）：

```
001.docx → 甲方（客户）：示例测试钱磊敏，客户编号 000001。签约日期：2025年1月4日；金额 1,123.45 元
002.docx → 甲方（客户）：示例测试孙涛强，客户编号 000002。签约日期：2025年1月7日；金额 1,246.90 元
003.docx → 甲方（客户）：示例测试李桂洋，客户编号 000003。签约日期：2025年1月10日；金额 1,370.35 元
```

负面用例（**按预期被拒绝即为正确结果**）：

| 用例 | 实测 |
|---|---|
| 无凭据调 `/api/v1/projects` | 401 |
| 只有 `openid` 的身份令牌调业务接口 | 401 `凭据无效、已过期或已撤销` |
| 同一 `Idempotency-Key` 两次提交 | 同一个 job id（`j_e449f75b9b70`） |
| 停用服务账户后继续用已签发的令牌 | 401 `client_unavailable` |
| 平台管理员读工作空间 | 只返回 `projects: 1` 这样的**数量**，无项目名称与内容 |

## 浏览器实跑

门户首页 → 登录（账户服务，输一次口令）→ 门户看到 Synwevo Docs 卡片 → 打开工作台（未再要口令）→
批量生成 10 份 → 结果页 10/10 成功且逐条「已核验」→ 取回产物核对内容。

清空 Synwevo Docs 会话后直接打开深链接 `http://docweave.localhost:8765/p/p_23578f09c418` →
跳到账户服务登录 → 登录后**回到该深链接本身**（`location.href` 实测）。

停掉门户之后：`portal /healthz → 000`（不可达），`docweave /healthz → 200`，
`accounts /healthz → 200`，Synwevo Docs 工作台照常打开与操作。

账户与安全页、平台管理后台（概览 / 账户 / 工作空间 / 审计）在桌面与 375px 窄屏下都可操作，
静态资源与样式正常。

## 未验证 / 外部条件

* 真实认证器的 Passkey 互操作（需要用户生物特征，不擅自发起）；本轮仍由软件虚拟认证器驱动。
* 真实邮件送达；本轮使用本地投递目录。
* 真实业务文件的结构差异；本轮全部使用合成夹具。
* **未部署到服务器**，未推送任何提交，未修改远程仓库。

---

# Synwevo 品牌统一与仓库迁移轮（2026-09-24）

环境：macOS（Apple Silicon），Python 3.14.5（仓库根目录 `.venv`，**本轮重建**），Node 22 / npm workspaces。
运行验证都指向隔离实例 `/tmp/synwevo-live`；真实业务库全程未被任何服务打开。

## 实际执行的命令与结果

```bash
.venv/bin/python -m pytest
# → 665 passed, 1 skipped（改名前是 650 passed, 1 skipped；新增 15 条兼容规则测试）

.venv/bin/python scripts/check_boundaries.py
# → 检查了 159 个文件，覆盖 7 个模块边界。没有跨应用导入、没有 sys.path 改写、
#   没有机器绝对路径、没有指向旧目录的链接。

.venv/bin/python scripts/check_brand.py
# → 旧品牌字符串全部归类，0 处未分类。

npm run typecheck && npm run build
# → 三个外壳（@synwevo/docs-web、@synwevo/account-web、@synwevo/workspace-web）全部通过

SYNWEVO_DATA_DIR=/tmp/synwevo-live .venv/bin/python scripts/platform.py up --init
# → accounts / portal / docs 三个角色启动，打印：
#     Synwevo Workspace   http://portal.localhost:8769
#     Synwevo Docs        http://docweave.localhost:8765
#     Synwevo Account     http://accounts.localhost:8770/login

.venv/bin/python tests/integration/verify_sso.py --email owner@example.test --password '***'
# → 38/38 项通过；其中「平台名称可配置：Synwevo」
```

## 配置兼容（这是本轮唯一有外部使用者的兼容面）

只用旧名字启动一个**真实进程**：

```bash
env DOCWEAVE_DATA_DIR=/tmp/synwevo-legacy DOCWEAVE_UI_AUTH=session DOCWEAVE_ROLE=all     .venv/bin/python -c "from synwevo_server.app import create_app; ..."
```

输出：

```
[synwevo] DOCWEAVE_ROLE 是旧名字，仍然可用；新名字是 SYNWEVO_ROLE。
[synwevo] DOCWEAVE_DATA_DIR 是旧名字，仍然可用；新名字是 SYNWEVO_DATA_DIR。
[synwevo] DOCWEAVE_UI_AUTH 是旧名字，仍然可用；新名字是 SYNWEVO_UI_AUTH。
  state: {'mode': 'session', 'role': 'all', 'platform_name': 'Synwevo'}
  healthz: 200
  data dir came from the legacy name: /tmp/synwevo-legacy
```

冲突时拒绝而不是猜：

```bash
env SYNWEVO_DATA_DIR=/tmp/a DOCWEAVE_DATA_DIR=/tmp/b .venv/bin/python -c "…create_app()"
# → synwevo_core.env.ConfigConflict: SYNWEVO_DATA_DIR='/tmp/a' 与 DOCWEAVE_DATA_DIR='/tmp/b'
#   同时设置且不一致。这一项关系到数据位置、密钥或信任边界，不能替你猜一个。
```

`scripts/platform.py status` 在同样的条件下也拒绝。

## 机器自动化（无浏览器、无 Cookie）

```bash
.venv/bin/python tests/integration/automation_example.py \
    --base-url http://docweave.localhost:8765 --issuer http://accounts.localhost:8770 \
    --client-id nightly-batch --client-secret '***' \
    --source testdata/测试_客户100.xlsx --template testdata/测试_合同模板.docx \
    --definition docs/integration/example-definition.json --out /tmp/syn-automation
# → 任务 completed，样本 10，成功 10，输出 10 个 .docx
```

打开产物逐条核对：

```
001.docx → 甲方（客户）：示例测试钱磊敏，客户编号 000001。签约日期：2025年1月4日；金额 1,123.45 元
002.docx → 甲方（客户）：示例测试孙涛强，客户编号 000002。签约日期：2025年1月7日；金额 1,246.90 元
```

负面用例：无凭据 → 401。

## 浏览器实跑

| 页面 | 观察到的 |
|---|---|
| `portal.localhost:8769` | 标签页标题 `Synwevo Workspace`，页头 `Synwevo` |
| 登录页 | 品牌标记 `◫ Synwevo`，标题 `Synwevo Account · 账户与安全` |
| 门户首页（登录后） | 应用卡片显示 **Synwevo Docs**（数据库 `apps.name` 已由 `register()` 精确更新） |
| `docweave.localhost:8765` | 标题 `Synwevo Docs · 数据映射与批量生成`，左上角 `S Synwevo Docs`，返回链接 `← Synwevo` |
| 账户与安全 | 账户、工作空间、登录设备、Passkey、两步验证均可操作 |
| 平台管理后台 | 概览 / 账户 / 工作空间 / 审计 |
| 375px 窄屏 | 首次发现顶栏折成三行并压住内容 → 把长副标题改成 `desktop-only` 后复测正常 |

## 口令黑名单

实跑时真的挡住了一次：用 `Synwevo-Local-Demo-2026!` 做 bootstrap 口令被拒——
「口令不能包含本服务的名称，请换一个。」新品牌词已加入黑名单，旧品牌词保留。

## Git

```bash
git remote get-url --all origin        # → git@github.com:WeGreen-AI/Synwevo.git
git remote get-url --push --all origin # → git@github.com:WeGreen-AI/Synwevo.git
git config --local --get-all remote.origin.pushurl   # → 未设置
git config --get remote.pushDefault                  # → 本地与全局均未设置
git config --get-regexp 'url\..*\.insteadof'         # → 无
git ls-remote git@github.com:WeGreen-AI/Synwevo.git  # → 退出码 0，零个引用（存在且为空）
git ls-remote git@github.com:WeGreen-AI/DocWeave.git # → 退出码 128，Repository not found
```

旧的 `refs/remotes/origin/*` 已移到 `refs/docweave-origin/*` 保留为历史依据。目标仓库为空，
所以 `@{upstream}` 当前无法解析——如实保留这个状态，没有伪造远端分支。

**本轮未推送、未部署。**

## 未验证 / 外部条件

* 真实认证器的 Passkey 互操作（需要用户生物特征）；仍由软件虚拟认证器驱动。
* 真实邮件送达；本轮使用本地投递目录。发件显示名已改为 `Synwevo`，正文署名已改。
* 目标仓库「为空」是当前观察；推送之后的表现需要推送时再验证。
* 旧仓库 `Repository not found` 无法区分是改名、删除还是权限变化——只记录现象。
