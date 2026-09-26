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
