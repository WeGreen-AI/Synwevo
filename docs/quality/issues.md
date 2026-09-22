# 问题清单（Issue Register）

状态：`已确认` 已复现/已从代码确认 · `风险` 待验证 · `未完成` 原需求未完成 · `外部阻碍` · `格式边界` · `已修复`（附验证证据）

证据来源说明：本仓库没有 Issue/PR/CI 记录（2026-09-23 本地初始化 Git；GitHub `WeGreen-AI/DocWeave` 在当前环境无凭据，`git ls-remote` 返回认证失败，无法读取远端内容）。以下问题来自代码审查、测试运行、真实文件验证与界面走查。

| ID | 优先级 | 类别 | 用户影响 | 发现来源 | 复现 / 证据 | 根因 | 修复方案 | 验证证据 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| DW-001 | P0 | 任务 | 重复提交可能创建两个任务；相同请求键但内容不同时静默返回旧任务 | 代码审查 `jobs.py create()` | 先 `SELECT` 再 `INSERT`，无作用域；键相同、参数不同返回已存在任务 | 进程内检查 + 全局唯一列 | 持久化幂等表（租户+键 唯一约束，保存规范化请求哈希），同键不同内容返回 409，保留期可配 | 见下方「修复记录」 | 已修复 |
| DW-002 | P0 | 任务 | 取消请求在最后一项处理期间到达时，任务仍可能被标为「已完成」 | 代码审查 `_run()` 收尾 | 收尾时无条件写 `completed`，不检查 `cancel_requested` | 终态写入非条件更新 | 原子条件更新裁决终态；新增「取消中」状态 | 同上 | 已修复 |
| DW-003 | P0 | 任务 | 多进程/多实例共用数据目录时，同一任务可能被两个工作进程同时执行 | 代码审查 `_loop()` | 选取 `queued` 后直接执行，无认领 | 无原子认领/租约 | `UPDATE … WHERE status='queued'` 原子认领 + 租约与心跳 | 同上 | 已修复 |
| DW-004 | P0 | 结果 | 进程在「写入产物」与「标记成功」之间中断时，恢复后重复生成，ZIP/清单出现重复文件 | 代码审查 `_run()`/`zip()` | 产物与状态分两次提交；ZIP 取全部产物 | 缺少事务；未过滤未发布产物 | 事务内发布产物+状态；恢复时清理未发布产物；下载只取已发布 | 同上 | 已修复 |
| DW-005 | P1 | 任务 | 运行线程异常退出后任务永久停在「进行中」，直到重启 | 代码审查 | 无心跳、无看门狗 | 仅启动时恢复 | 租约过期自动恢复 + 看门狗重启线程；`/readyz` 反映运行线程状态 | 同上 | 已修复 |
| DW-006 | P1 | 任务 | 任务级异常后样本项停在「进行中」 | 代码审查 `_loop()` 异常分支 | 只改任务状态 | 缺少项状态收尾 | 异常时把运行中项恢复为待处理或失败并写原因 | 同上 | 已修复 |
| DW-007 | P1 | 任务 | 全部样本失败时任务显示「部分失败」，自动化无法区分 | 代码审查 | 状态只分 completed / completed_with_errors | 状态模型不完整 | 全部失败 → `failed`；部分 → `completed_with_errors` | 同上 | 已修复 |
| DW-008 | P1 | 任务 | 重试在原任务上直接改状态，没有记录重试尝试，重复点击可重复重试；运行中也允许重试 | 代码审查 `retry()` | 无状态检查、无运行记录 | 缺少运行记录模型 | 新增 `job_runs`（初次/重试运行记录）；只允许终态重试；重试支持幂等键 | 同上 | 已修复 |
| DW-009 | P1 | 下载 | 大任务 ZIP 在内存中整体生成；无长度、无断点续传 | 代码审查 `zip()` | 5 000 份任务打包时占用与产物总大小相当的内存 | 内存打包 | 流式写入临时文件后以文件响应（含长度、Range） | 同上 | 已修复 |
| DW-010 | P1 | 安全 | 交互式接口文档 `/api/docs`、`/api/openapi.json` 匿名开放；界面接口在非本机监听时无任何访问控制 | 代码审查 `create_app` | 访问即得 | 默认开放 | 接口文档默认关闭（配置开启）；非本机监听必须显式选择界面鉴权方式；机器接口始终需要密钥 | 同上 | 已修复 |
| DW-011 | P2 | 性能 | 所有数据库读写串行（全局锁），高并发时响应变慢 | 代码审查 `Store` | 读也持锁 | 单连接共享锁 | 读写分离：读不持写锁，写使用 `BEGIN IMMEDIATE` 事务 | 待测 | 风险 |
| DW-012 | P1 | 接口 | 错误响应无请求追踪 ID，格式不统一 | 代码审查 | `{"error","message","detail"}` | 无统一中间件 | 统一错误结构 + `X-Request-ID` | 同上 | 已修复 |
| DW-030 | P0 | 未完成 | 没有可供外部程序调用的版本化接口、机器鉴权、事件、Webhook | 需求 | — | 原需求未含 | `/api/v1` + API 密钥（CLI 管理）+ 事件 + 签名 Webhook | 同上 | 已修复 |
| DW-031 | P1 | 未完成 | 没有存活/就绪检查 | 需求 | — | — | `/healthz`、`/readyz` | 同上 | 已修复 |
| DW-032 | P1 | 未完成 | 缺少端口、接口、自动化调用、运维文档与 OpenAPI 文件 | 需求 | — | — | `docs/integration/*`、`docs/operations.md` | 同上 | 已修复 |
| DW-040 | P1 | 性能 | 打开或滚动大表时每翻一页都卡顿约 0.5 秒 | 真实文件实测（合成 20000×30 / 3.5 MB XLSX，运行中的服务） | `curl /api/sources/{id}/view?offset=N&limit=200` 连续请求：400–650 ms/次（预热后仍然） | `workbook_window()` 每次请求都用 `Workbook.from_json()` 重建 60 万个单元格的对象模型，绕开了 `SourceDoc` 已有的缓存 | `workbook_window()` 直接接收已解析的 `Workbook`（仍兼容传入 JSON）；`views.source_view` 传 `doc.workbook` | 同一文件、同一服务：预热后每页 **11–12 ms**（首页 380 ms 为读取并解析 blob）；`tests/test_large_sheet.py` 统计重建次数，恢复旧调用后该测试失败（已验证） | 已修复 |
| DW-041 | P2 | 性能 | 打开表格文件时同一页数据被请求两次，大文件多花约 300 ms 和 133 KB | 浏览器网络面板（同一 20000×30 文件） | `view?offset=0&limit=200` 与 `view?sheet=明细&offset=0&limit=200` 两次返回相同内容 | `GridView` 首次以 `sheet=null` 加载，拿到服务端选定的工作表后回调父组件，`sheet` 变化又触发同一个 effect | 记住「本次 reloadKey 已加载的工作表」，与之相同时不再重新拉取 | 打开同一文件的接口流量 269 KB → **136 KB**，重复请求消失（网络面板复测） | 已修复 |
| DW-042 | P2 | 测试 | 从 `backend/` 运行测试时 4 个「每块一份」用例失败，掩盖真实结果 | `pytest` 在 `backend/` 下运行 | `FileNotFoundError: 'testdata/测试_登记卡模板.docx'` | 测试内写死相对路径，只在仓库根目录运行时成立 | 改用 `fixtures` fixture 的绝对路径 | `backend/.venv/bin/python -m pytest -q`（在 `backend/` 下）→ 172 passed, 1 skipped | 已修复 |
| DW-043 | P2 | 界面 | 1280×800 窗口下两侧面板占 43%，核心文档区被挤压 | 界面走查（实测 `getBoundingClientRect`） | 文件列表 220 + 映射面板 330 = 550 px；两个文档面板各 362 px | 面板宽度写死，且右侧面板不可调整 | 右侧面板增加拖拽调宽（记住宽度），并按窗口宽度收紧默认值 | 同一窗口：文档区占比 57% → **62%**，收起面板后可达约 82%；拖拽后宽度写入本地存储并在重载后保留（实测 240 px） | 已修复 |
| DW-044 | P2 | 操作效率 | 映射只能逐条用鼠标操作：没有键盘导航、没有多选，批量启用/禁用/删除要点很多次 | 界面走查 + 原需求（批量字段操作） | 10 条映射全部禁用需要 10 次点击、10 次确认 | 映射列表是普通 div 列表，无选择模型 | 列表改为 `role="listbox"`：↑↓/J K 移动并联动高亮两侧文档，X 多选，Shift 范围选，⌘A 全选，D 启用/禁用，Delete 删除；批量操作走同一个事务保存，一次撤销即可回滚 | 浏览器实测：J/X 两次后 `aria-selected` 为 `[false,true,true,false,false]`、批量条显示「已选 2 / 5」；⌘A 选中 5 条且不选中页面文字；D 后服务端 `enabled` 全为 false、备注「禁用 5 个映射（#1…#5）」、rev 5→6；⌘Z 后全部恢复且 rev 回到 5 | 已修复 |
| DW-045 | P3 | 界面 | 深色主题不完整：徽章、步骤条、选择栏、文件项等仍用写死的浅色 | 界面走查 | 12 处硬编码色值 | 设计令牌未覆盖全部组件 | 全部改为令牌；文档纸张仍固定为 paper 令牌 | 深色下实测：`.topbar` `rgb(22,28,37)`、`.side.right` `rgb(27,34,44)`，而 `.gcell`/`.doc-page` 仍为 `rgb(255,255,255)` + `rgb(17,20,24)` | 已修复 |
| DW-046 | P3 | 操作效率 | 关闭后重开项目，栏宽、缩放、折叠状态、连线开关全部回到默认 | 界面走查 | 仅左栏宽度被记住（`dw.leftW`） | 其余状态未持久化 | 统一 `remember/recall`（存储不可用时静默降级），记住栏宽、面板宽、折叠、缩放、连线、当前标签页、指引条 | 重载后实测保留（`docweave.sideW` = 240 等） | 已修复 |
| DW-047 | P0 | 安全 | 绑定 `0.0.0.0` 时界面（含全部来源文件、模板与生成结果）对局域网匿名开放，而文档声称会拒绝启动 | 交付前实跑验证：`DOCWEAVE_HOST=0.0.0.0 python backend/run.py` | 服务正常启动并监听 `0.0.0.0:8798`，无任何界面鉴权；`docs/integration/ports.md` 当时写的是「会拒绝启动」 | `check_listen_config()` 已实现但**没有任何调用点**（死代码） | `backend/run.py` 启动前调用；容器内（`DOCWEAVE_IN_CONTAINER=1`）改为打印警告，因为容器的暴露范围由端口映射决定，进程无法判断；文档按实际行为改写 | 实测：`DOCWEAVE_HOST=0.0.0.0` 退出码 1 并打印三种处理方式；加 `DOCWEAVE_UI_AUTH=proxy` 后正常启动（`Uvicorn running on http://0.0.0.0:8798`）。`tests/test_listen_guard.py` 12 项，含直接运行 `backend/run.py` 断言退出码非 0 | 已修复 |

## 上一轮已修复（保留为回归依据）

| ID | 问题 | 根因 | 修复 | 回归测试 |
|---|---|---|---|---|
| DW-020 | 无头 LibreOffice 用无中文字形的字体排版中文：导出 PDF 中文不可见、分页计算错误 | 无头模式未加载系统字体 | 私有配置目录注入中文字体（系统字体优先，内置 Droid Sans Fallback 兜底） | `test_layout.py::test_libreoffice_lays_out_chinese_with_a_cjk_font` |
| DW-021 | 中文 OCR 实际只用英文模型（识别结果为乱码） | `--tessdata-dir` 放在 `tsv` 配置名之后被忽略 | 参数顺序修正，改用 `tessedit_create_tsv=1`；加载失败时报错 | `test_image_template.py`（OCR 核对输出）、`test_acceptance_9_14.py::test_s10*` |
| DW-022 | 名称无扩展名且含中文的下载返回 400 | 响应头用非 ASCII 扩展名 | 扩展名清洗 | `test_regression_cases.py::test_desensitize_copies` |
| DW-023 | `ISERROR(1/0)` 返回错误值而非 TRUE | IS* 函数先求值参数导致错误传播 | IS* 改为惰性求值 | `test_formula.py`（与 LibreOffice 交叉核对 86 个公式） |
| DW-024 | 图片模板重复明细单元格被涂黑 | 背景采样落在表格线上 | 采样区域改为单元格内边带，按亮色主体取背景 | `test_image_template.py::test_png_template_end_to_end` |

## 修复记录

### 2026-09-23 任务可靠性（DW-001 … DW-009、DW-011 部分）

- 存储层：`Store.tx()` 显式事务（`BEGIN IMMEDIATE`，进程内串行 + SQLite 跨进程锁），读取不再持写锁；schema 升级到 v2（租户、租约、运行记录、事件、幂等表、API 密钥、Webhook 投递），迁移在真实 v1 数据副本上验证（29 个项目 / 13 个任务全部保留，可重复执行）。
- DW-001：幂等表以 (租户, 作用域, 键) 为主键；同键同内容返回原任务并标记 `idempotent_replay`，同键不同内容返回 409 `idempotency_conflict`；并发同键由唯一约束裁决；保留期 `DOCWEAVE_IDEMPOTENCY_TTL_HOURS`（默认 24 小时）后自动清理。
- DW-002：取消与完成在同一事务内裁决；新增「取消中」状态；对终态任务取消返回 `accepted:false, reason:already_terminal`；重复取消稳定。
- DW-003：`UPDATE … WHERE status='queued'` 原子认领 + 租约（`DOCWEAVE_JOB_LEASE_S`，默认 30 秒）+ 心跳。
- DW-004：产物写入与样本「成功」状态在同一事务内发布；重复发布被状态与租约条件拒绝；下载与清单只取已发布且未删除的产物。
- DW-005：租约过期（或同机进程已不存在）自动恢复；看门狗线程重启运行线程。
- DW-006：任务级异常把运行中/待处理样本标为失败并写明原因。
- DW-007：全部样本失败 → 任务状态 `failed`；部分失败 → `completed_with_errors`。
- DW-008：新增 `job_runs` 运行记录（初次/重试）；仅终态可重试；重试支持幂等键。
- DW-009：ZIP 改为写入临时文件后流式下载（内存不随产物总量增长）。

验证：`backend/.venv/bin/python -m pytest backend/tests -q` → **154 passed, 1 skipped**（其中 `backend/tests/test_job_reliability.py` 10 项针对上述语义）。

### 2026-09-23 自动化接口与文档（DW-010、DW-012、DW-030 … DW-032）

- `/api/v1`（39 条路由）：能力查询、文件、模板与字段、规则与映射、声明式定义导入导出、预检、任务（提交/查询/事件/取消/重试）、产物与打包下载、`report.csv`、临时下载链接。UI 与自动化共用同一套引擎实现。
- 鉴权：API 密钥 `dw_<id>_<secret>`（仅存哈希）、租户隔离、`read`/`write`/`admin` 权限范围、过期与吊销、令牌桶限流；界面访问模式 `none`/`basic`/`proxy`，非本机监听且为 `none` 时拒绝启动。密钥只能用 `backend/manage.py apikey` 管理，产品界面不暴露任何入口。
- 事件与 Webhook：`job_events` 稳定事件结构（事件 ID/类型/版本/任务 ID/发生时间/状态），签名 Webhook（HMAC-SHA256、时间戳、退避重试、投递记录、SSRF 允许列表），默认关闭。
- 健康检查：`/healthz`（进程存活）与 `/readyz`（存储可写、运行线程存活），不返回敏感诊断。
- 文档：`docs/integration/ports.md`、`api.md`、`automation.md`、`openapi.yaml`（由实现导出）、`docs/operations.md`、`.env.example`、`scripts/automation_example.py`。

验证：`backend/tests/test_api_v1.py`（9）、`test_job_reliability.py`（10）、`test_webhooks.py`（5）、`test_api_docs.py`（3）；以及对运行中的服务按 `docs/integration/automation.md` 实跑一遍无界面流程（上传 → 模板 → 规则 → 映射 → 预检 5 条有效 → 提交 → 5 份产物 → ZIP 185 190 字节，内容逐字段核对正确）。

### 2026-09-23 界面、操作与性能（DW-040 … DW-046）

见上表各行的验证证据。本轮界面改动全部在 `frontend/src` 内，未新增任何 API/密钥/Webhook/端口相关的界面入口。

实测环境（所有界面与接口数字均来自这台机器）：Apple arm64 / macOS Darwin 27.0.0 / 15 核 / Python 3.14.5；浏览器 Chrome 152，窗口 1280×800；服务为 `backend/run.py` 单进程（127.0.0.1:8765）；输入为合成测试文件。

| 指标 | 方法 | 结果 |
|---|---|---|
| 首屏可操作（100 行 × 5 列项目） | `PerformanceNavigationTiming` + 接口耗时 | DOMContentLoaded 32 ms；项目/视图/定位接口各 2–11 ms |
| 打开 20000×30（3.5 MB）表格 | 同上 | 上传+解析 6.63 s（服务端）；首个网格窗口 380 ms；其后每页 11–12 ms |
| 单元格选区响应 | 页面内实测：事件派发 → React 提交 → 强制样式/布局 | 中位 5.9 ms，p95 6.7 ms（n=25） |
| 映射列表键盘移动（联动双侧高亮） | 同上 | 中位 5.1 ms，p95 5.9 ms（n=25） |
| 大表虚拟滚动 | 同上 | 中位 5.3 ms，p95 5.5 ms（n=25，仅渲染 145 个单元格节点） |
| 校验 / 样本 / 预览 / 定位接口 | 浏览器 `fetch` 计时 | 8 / 4 / 29 / 2 ms |
| 页面内存 | `performance.memory` | 打开大表后 7–10 MB |
| 批量生成 | `scripts/benchmark.py`（真实处理链路） | 100 份 DOCX 1.21 s（12.1 ms/份）；1 000 份 11.12 s；5 000 份 65.35 s（13.1 ms/份，峰值 RSS 452 MB）；PDF 模板 300 份 22.97 s（76.6 ms/份）；DOCX+PDF 双格式 20 样本 14.2 s（355 ms/份，LibreOffice 转换为主） |

说明：界面交互数字为**页面内测量**（事件处理 + React 提交 + 强制样式/布局），不含操作系统输入延迟与合成器绘制；测量时浏览器面板处于隐藏状态，`requestAnimationFrame` 被节流，因此没有给出逐帧绘制时间——这一项未测量，不作结论。约 100 ms 的交互反馈目标在上述已测项目上达成，但这是本机实测值，不代表所有机器与文件规模。
