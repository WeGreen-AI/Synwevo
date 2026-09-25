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
