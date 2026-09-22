# 验证记录（实际执行的命令与结果）

本文只记录**实际运行过**的检查及其输出。未运行的检查在文末「未覆盖」一节如实列出，不计入通过项。
问题与修复证据见 [issues.md](issues.md)。

## 环境

| 项 | 值 |
|---|---|
| 机器 | Apple Silicon（arm64），15 核 |
| 系统 | macOS，Darwin 27.0.0 |
| Python | 3.14.5（`backend/.venv`） |
| 前端 | Node / Vite 8、TypeScript、React 19 |
| 浏览器 | Google Chrome 152（Playwright `channel="chrome"`，headless） |
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

**结果：185 passed, 1 skipped**（约 85 秒）。跳过项为需要外部依赖的用例（见 `docs/TESTING.md`）。

本轮新增/修改的用例：

| 文件 | 数量 | 锁定的行为 |
|---|---|---|
| `tests/test_listen_guard.py` | 6（12 个参数化实例） | 非本机监听 + 无界面鉴权必须拒绝启动；容器内改为警告；直接运行 `backend/run.py` 断言退出码非 0 |
| `tests/test_large_sheet.py` | 1 | 大表分页复用已解析工作簿（统计重建次数） |
| `tests/test_api_v1.py::test_every_response_carries_the_trace_id` | 1 | 鉴权失败的响应也带 `X-Request-ID`，且与响应体一致、可由调用方指定 |
| `tests/test_per_block.py` | 4（修复） | 不再依赖运行目录 |

两个新回归测试都验证过「没有修复时会失败」：恢复旧调用后 `test_large_sheet.py` 报「分页时重建了 6 次工作簿对象」；修复前 `DOCWEAVE_HOST=0.0.0.0` 实际可以启动并提供服务。

## 2. 构建与类型检查

```bash
cd frontend && npx tsc --noEmit && npm run build
```

**结果：类型检查无输出（通过）；生产构建成功**（`✓ built in 98–217ms`，`index.js` 471 KB / gzip 146 KB，`index.css` 28.8 KB / gzip 6.7 KB）。

## 3. 干净环境启动与部署一致性

```bash
DOCWEAVE_DATA_DIR=<空目录> DOCWEAVE_PORT=8799 backend/.venv/bin/python backend/run.py
```

| 检查 | 结果 |
|---|---|
| `/healthz` | `{"status":"ok","version":"1.0.0"}` |
| `/readyz` | `{"status":"ready","checks":{"database":"ok","storage":"ok","worker":"ok"},"schema_version":2}` |
| 界面首页 | HTTP 200 |
| `/api/v1/capabilities` 无密钥 | HTTP 401 |
| `/api/docs` 默认 | HTTP 404（未配置 `DOCWEAVE_API_DOCS`） |
| 新建数据目录内容 | `blobs/ docweave.sqlite3(+wal,shm) tmp/` |

监听与鉴权守卫：

| 场景 | 结果 |
|---|---|
| `DOCWEAVE_HOST=0.0.0.0`，`DOCWEAVE_UI_AUTH` 未设 | **退出码 1**，打印「拒绝启动…」及三种处理方式 |
| 同上 + `DOCWEAVE_UI_AUTH=proxy` | 正常启动：`Uvicorn running on http://0.0.0.0:8798` |
| 容器（`DOCWEAVE_IN_CONTAINER=1`） | 打印警告而不拒绝（暴露范围由端口映射决定） |

## 4. 无界面调用（不打开浏览器）

按 `docs/integration/automation.md` 执行：

```bash
python backend/manage.py apikey create --name 验收 --scopes read,write
export DOCWEAVE_API_KEY=dw_...
backend/.venv/bin/python scripts/automation_example.py \
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

独立实例（`DOCWEAVE_PORT=8788`，独立数据目录）+ 一个会校验签名的本地接收端。

| 检查 | 结果 |
|---|---|
| 默认拒绝 `http://` 回调 | 「回调默认要求 https；确需 http 时设置 `DOCWEAVE_WEBHOOK_ALLOW_HTTP=1`」 |
| 默认拒绝内网地址 | `https://127.0.0.1:8877` → 「解析到内网地址 127.0.0.1；请加入 `DOCWEAVE_WEBHOOK_ALLOW_PRIVATE`」 |
| 云元数据地址 `169.254.169.254` | 即使配置了 `127.0.0.0/8` 允许列表仍被拒绝（允许列表只放行明确网段） |
| 配置允许列表后投递 | 收到 `job.completed`，HMAC 签名校验 **通过**；伪造签名被接收端拒绝 |
| 接收端返回 503 | 30 秒后按退避重试，第二次 200；两次的 `event_id` 与 `delivery` **相同**（接收端可据此去重） |
| 投递记录 | `webhook_deliveries`：`status=delivered, attempts=2, last_code=200` |
| 回调失败期间产物 | 全程可查询与下载（自动化脚本在首次 503 之后仍成功下载全部产物） |
| 事件序列 | `job.created → job.started → job.completed` |

## 9. 浏览器端到端

```bash
backend/.venv/bin/python scripts/ui_e2e.py           # 13.6s 通过
backend/.venv/bin/python scripts/ui_e2e_more.py      # 10.2s 通过（4 条流程）
backend/.venv/bin/python scripts/ui_e2e_keyboard.py  #  4.1s 通过（4 条流程）
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

界面交互数字为**页面内测量**：事件派发 → React 提交 → 强制样式/布局。**不含**操作系统输入延迟与合成器绘制。

| 指标 | 结果 |
|---|---|
| 首屏（100 行项目）DOMContentLoaded | 32 ms |
| 单元格选区响应 | 中位 5.9 ms，p95 6.7 ms（n=25） |
| 映射列表键盘移动（联动双侧高亮） | 中位 5.1 ms，p95 5.9 ms（n=25） |
| 大表虚拟滚动 | 中位 5.3 ms，p95 5.5 ms（n=25） |
| 20000×30 / 3.5 MB 表上传+解析 | 6.63 s（服务端） |
| 该表首个网格窗口 / 其后每页 | 380 ms / **11–12 ms**（修复前 400–650 ms） |
| 打开该文件的接口流量 | **136 KB**（修复前 269 KB） |
| 校验 / 样本 / 预览 / 定位接口 | 8 / 4 / 29 / 2 ms |
| 页面内存 | 7–10 MB（20000×30 表只渲染 145 个单元格节点） |

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

「关键交互反馈约 100 ms 内」这一目标在上述**已测项目**上达成。这些是本机实测值，不代表其他机器或更大规模。

## 12. 可访问性与主题

| 检查 | 结果 |
|---|---|
| 键盘完成映射浏览、多选、启用/禁用、删除、撤销 | 通过（`ui_e2e_keyboard.py` F） |
| 焦点管理：点击条目后列表可直接用键盘 | 通过（断言 `document.activeElement` 为 `.maplist`） |
| 非颜色提示 | 选中有勾选标记与 `aria-selected`，禁用有「已禁用」徽章，错误有文字说明 |
| 语义角色 | 映射列表 `listbox`/`option`/`aria-multiselectable`，批量条 `toolbar`，命令面板 `dialog`+`listbox` |
| 减少动态效果 | `prefers-reduced-motion: reduce` 下 `--dur-2` = `0ms`（实测） |
| 深色主题不影响文档 | `.doc-page`、`.gcell` 在深浅色下均为 `rgb(255,255,255)` / `rgb(17,20,24)`（实测） |
| 中文输入法 | 全局按键处理检查 `isComposing` / `keyCode 229`；输入框内不触发字母快捷键 |

## 13. 未覆盖 / 未测量（如实记录）

| 项 | 原因 | 影响 |
|---|---|---|
| 逐帧绘制时间（滚动/拖拽的实际帧率） | 测量时浏览器面板处于隐藏状态，`requestAnimationFrame` 被节流到约 0.5 Hz，测得的 2 s/帧是节流值而非真实帧时间 | 只能说明 JS 与布局成本很低，**不能**据此声称帧率。需在可见窗口中补测 |
| Microsoft Word / Excel / PowerPoint 实机打开 | 自动化链路用 LibreOffice 打开与渲染；Office 需人工或桌面自动化 | 见 `docs/ACCEPTANCE.md` 第 13 项（上一轮人工检查记录） |
| 真实业务样本 | 未获得用户的（脱敏）真实文件 | 全部验收基于合成数据；真实文件的结构差异未覆盖。界面「保存为回归用例」可在拿到样本后直接补充 |
| 除 Chrome 外的浏览器 | 未运行 | Firefox / Safari / Edge **未测试**，不声称兼容 |
| 多实例共享数据目录的长时间并发 | 只做了同进程 4 线程并发与单次中断恢复 | 多进程部署的长稳未验证 |
| 大规模并发负载（多客户端同时提交） | 未运行 | 上表所有数字均为单客户端、单进程 |

## 14. 发布判断

* 本轮范围内**已确认缺陷为 0**：`docs/quality/issues.md` 中 DW-001…DW-012、DW-030…DW-032、DW-040…DW-048 全部为「已修复」并附验证证据。
* 约定检查全部执行并通过：185 项后端测试、3 套浏览器端到端、类型检查、生产构建、干净环境启动、无界面调用、界面/接口一致、权限与输入、幂等并发、取消语义、Webhook 全链路、持续使用资源。
* 仍然存在的限制与未测量项见第 13 节与 `README.md`「已知限制」，**不计入已交付**。
* 结论：可以发布到自托管环境。对外提供服务前需按 `docs/DEPLOY.md` 配置反向代理与界面鉴权，并确认 PyMuPDF 的许可证义务。
