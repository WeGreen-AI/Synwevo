# 问题清单（Issue Register）

状态：`已确认` 已复现/已从代码确认 · `风险` 待验证 · `未完成` 原需求未完成 · `外部阻碍` · `格式边界` · `已修复`（附验证证据）

当前轮（2026-09-25）的范围、状态与执行证据统一维护在 [handoff.md](handoff.md)。以下 DW 表及修复记录是历史记录，日期、旧路径和旧 CLI-only UI 约束不代表当前状态；管理员 API 台的当前权限以 [permissions.md](../security/permissions.md) 为准。

## 2026-09-25 增量安全审查

| 问题 | 修复与验证入口 | 状态 |
|---|---|---|
| 预先注册他人邮箱、激活与旧口令登录并发 | 邮箱证明后设置最终口令；认证/签发同事务；`test_registration_activation.py`、`test_independent_identity_review.py` | 针对性回归通过 |
| 密码恢复并发生成多封互相失效的链接 | 冷却判定、令牌及入队原子化；真实并发回归 | 针对性回归通过 |
| 已删除账户通过旧备份重现资料/认证因素 | 终止删除重放幂等清除资料和认证因素；保留组织内容；`test_account_restore.py` | 实际隔离归档恢复通过 |
| 登出/重置后未消费授权码继续换令牌 | 在同事务检查原中央会话、消费代码、签发及关联；`test_independent_session_review.py` | 回归通过 |
| 撤销某 RP 会话仍留下设备中央会话 | 解析设备根并撤销其 RP、令牌；其他设备独立；撤销计数只统计实际改变 | 原失败及新回归通过 |
| 令牌兑换后、回调落地前发生撤销 | 回调事务再次检查用户/客户端关联和中央会话，记录真实设备根；`test_workspace_launch.py` | 登出/重置/过期负面及正向共25项通过 |
| 旧刷新/401覆盖新空间或身份 | JSON/XHR/Blob请求代次、上下文切换及清理业务视图 | 44项竞态检查、最终三语言组织/Docs/API浏览器通过 |
| 访问日志保存回调授权码等查询数据 | 共享工厂安装路径日志过滤；真实子进程请求带合成秘密复现并转绿 | 修复后15项日志/错误契约检查通过 |
| API 管理请求期间管理员或所有者批准被撤销 | 写事务内与异步返回前重新校验；调试超时/当前凭据再验；`test_api_platform_races.py` | 44项并发回归通过 |

## 历史问题与修复记录

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
| DW-047 | P0 | 安全 | 绑定 `0.0.0.0` 时界面（含全部来源文件、模板与生成结果）对局域网匿名开放，而文档声称会拒绝启动 | 交付前实跑验证：`DOCWEAVE_HOST=0.0.0.0 python -m synwevo_server.run` | 服务正常启动并监听 `0.0.0.0:8798`，无任何界面鉴权；`docs/integration/ports.md` 当时写的是「会拒绝启动」 | `check_listen_config()` 已实现但**没有任何调用点**（死代码） | `backend/run.py` 启动前调用；容器内（`DOCWEAVE_IN_CONTAINER=1`）改为打印警告，因为容器的暴露范围由端口映射决定，进程无法判断；文档按实际行为改写 | 实测：`DOCWEAVE_HOST=0.0.0.0` 退出码 1 并打印三种处理方式；加 `DOCWEAVE_UI_AUTH=proxy` 后正常启动（`Uvicorn running on http://0.0.0.0:8798`）。`tests/test_listen_guard.py` 12 项，含直接运行 `backend/run.py` 断言退出码非 0 | 已修复 |
| DW-048 | P2 | 接口 | 鉴权失败的响应没有 `X-Request-ID` 头，与文档「每个响应都带 X-Request-ID」不符，401 无法与服务端日志对上 | 交付前实跑验证 | `curl -D - /api/v1/capabilities`（无密钥）返回 401，响应头无 `x-request-id`（响应体内有 `request_id`） | 中间件在鉴权失败时提前返回，跳过了设置响应头的代码 | 把响应头设置提取为 `finish()`，三个返回路径（正常、鉴权失败、界面口令失败）都经过它 | 实测：401 的 `x-request-id` 与响应体 `request_id` 一致；调用方自带的 `X-Request-ID` 被原样回显；`tests/test_api_v1.py::test_every_response_carries_the_trace_id` | 已修复 |
| DW-049 | P1 | 接口 | 提交任务时把 `layout` 写错（如 `strict`、`fitt`、`OFF`），接口返回 202 成功但实际**完全不做分页检查**，调用方以为检查过，结果可能交付页数不对的文档 | 交付前实跑验证（对比 `error_policy` 的处理） | `POST /api/v1/projects/{id}/jobs` 传 `layout:"strict"` → HTTP 202，存储的 `options.layout` 为 `"off"`；而 `error_policy:"nonsense"` 正确返回 400 | 枚举值不在白名单时静默退回项目默认值（通常是 `off`），没有像 `error_policy` 那样校验 | 与 `error_policy` 一致：值不在 `off/warn/fit` 内返回 400 `bad_layout` 并指明字段；省略该键仍沿用项目设置 | `tests/test_job_reliability.py::test_unknown_layout_is_rejected_not_silently_downgraded`（5 个非法值全部 400，3 个合法值生效并落库，省略时回落项目默认）；恢复旧写法后该测试失败（已验证） | 已修复 |
| DW-050 | P1 | 数据正确性 | 字段的 `empty_policy` 写错时被静默替换：调用方要求「零条明细视为错误、不生成」，实际却按默认值生成了一份缺明细的文档，且接口报告成功 | DW-049 修复后按同一模式排查 | 创建重复明细字段时传 `empty_policy:"erro"` → 201 成功，落库为 `remove_row`；`PATCH .../slots/{id}` 同样不校验 `empty_policy`（只校验了 `separator` 与 `overflow`） | 创建路径 `req.get("empty_policy", "remove_row")` 完全不校验；`overflow`/`separator`/`align` 用 `X if X in (…) else 默认` 静默回落 | 新增 `_enum()` 统一校验，按字段种类使用对应枚举集（表格行 / 内容块 / PDF 区域），非法值抛 `bad_value` 并列出允许值；`update_slot` 补齐 `empty_policy`、`data_type`、`align`、`valign` 校验 | `tests/test_slot_options.py` 15 项（创建与 PATCH 的非法值全部 400 并带 `field`/`allowed`，合法值生效并落库）；移除修复后 10 项失败（已验证）。全量 201 passed, 1 skipped | 已修复 |
| DW-051 | **P0** | 错填数据 | 多来源合并方式写错时静默改为拼接：本应把 100 与 200 相加得 300 的字段，交付文档里是「100 200」，没有任何报错或警告 | 按 DW-049/050 模式继续排查数据正确性路径 | `transforms.combine([100,200], op="sumn")` 返回 `"100 200"`；`"SUM"`、`"add"`、`""` 同样落入拼接分支 | `combine()` 只判断 `sum` / `newline`，其余一律走 `sep.join()`；而同类的 `aggregate()` 对未知值抛 `TransformError`，两者不一致 | `combine()` 增加 `COMBINE_OPS` 白名单，未知合并方式抛 `TransformError` 并列出允许值，与 `aggregate()` 行为一致 | `tests/test_engine.py::test_combine_rejects_unknown_op`：`sum`/`concat`/`newline` 结果正确（300 / “100 200” / 换行），`sumn`/`SUM`/`add`/空串/`join` 全部抛错；并断言 `join` 仍是合法的**聚合**方式（两套枚举不混用）。全量 202 passed, 1 skipped | 已修复 |
| DW-052 | **P0** | 崩溃 | 无 PyMuPDF（pdfium）部署下，并发读取 PDF 会让**整个服务进程崩溃**（SIGBUS），所有在途请求与运行中的任务一起丢失 | 本轮按 §2 排查 pdfium 写入能力时的线程安全审查 | 8 线程 × 200 次 `page_words`/`pdf_text` 混合读取 → 进程退出码 **138（SIGBUS）**，无 Python 异常、无 traceback、无输出 | `pdfium_shim.py` 对 PDFium 的调用完全没有串行化；PDFium 官方明确不是线程安全的，且失败方式是 abort 而非抛异常。FastAPI 的同步路由跑在线程池上、任务执行器是另一个线程，并发是常态 | 新增进程级可重入锁 `pdfium_shim.LOCK`，覆盖页面的获取/使用/关闭以及文档打开、页数、关闭 | 同一复现脚本修复后 `ok=200/200`、退出码 0；`tests/test_pdfium_threading.py` 在**子进程**中跑该复现（回归时报测试失败而不是拖垮整个 pytest），去掉锁后该测试失败并指明 `pdfium_shim.LOCK` | 已修复 |
| DW-053 | P1 | 并发/部署 | 按 `.env.example` 配置 `DOCWEAVE_LO_PROFILE=/tmp/docweave-lo` 后，多个工作进程共用同一个 LibreOffice 配置目录，可能互相损坏配置并导致转换静默失败 | 版式探查（§3）审查 LibreOffice 调用隔离 | `converters.py` 默认按 PID 隔离，但一旦设置该环境变量就变成**固定共享路径**；`_LO_LOCK` 只能串行化同进程内的线程，跨进程无效 | 环境变量被当作「配置目录本身」而不是「父目录」 | 该变量改为父目录语义，实际始终使用 `<父目录>/profile-<PID>`；`.env.example` 说明同步更正 | 实测设置 `DOCWEAVE_LO_PROFILE=/tmp/docweave-lo` 后得到 `/tmp/docweave-lo/profile-71859`，按进程隔离保持不变 | 已修复 |
| DW-054 | P2 | 限制项 | 图片模板忽略可配置的像素上限：`DOCWEAVE_MAX_IMAGE_MPIX`（默认 80 MP）调高或调低对图片模板都无效，且未加载解压炸弹防护 | 图片模板可行性探查（§2.2） | `image_template.py` 写死 `MAX_PIXELS = 60_000_000`，并且 `open_image` 未导入 `engine/images.py`（该模块负责设置 `Image.MAX_IMAGE_PIXELS`） | 硬编码常量与集中式 `LIMITS` 不一致 | 改用 `LIMITS.max_image_pixels`，并导入 `images` 以确保 Pillow 的上限被设置 | 实测上限随 `DOCWEAVE_MAX_IMAGE_MPIX` 变化（当前 80 MP）；全量 213 passed | 已修复 |
| DW-055 | P2 | 界面 | 中文界面直接显示英文枚举（`completed_with_errors`），失败任务与取消/部分成功同为琥珀色，`每块一份` 样本模式显示为原始值，项目卡片**完全无法用键盘打开** | 首页体验探查（§6.2） | 首页 68 个项目卡片：`p.last_job.status` 原样输出；徽章三元表达式只区分 completed/running，其余一律 warn；`per_block` 不在标签表内；卡片是 `<div onClick>`，无 `tabIndex`/`role` | 三份互相分歧的状态映射（JobPage、Workspace、首页没有），且都与后端枚举不同步：`interrupted` 无任何代码写入，而后端真正会写的 `cancelling` 三处都缺失 | 新增 `frontend/src/lib/status.ts` 作为唯一映射（与 `jobs.py` 枚举对齐，未知值原样显示以便暴露缺口），三处统一引用；卡片加 `role="link"`、`tabIndex`、`aria-label` 与 Enter/Space 处理。**机器 API 的英文枚举不变** | 浏览器实测真实 68 个项目：68/68 可键盘聚焦、`role=link`、徽章为「已完成」「已取消」且色调 ok/warn、无任何英文枚举泄漏；三套 E2E 通过 | 已修复 |
| DW-056 | P1 | 未完成的原需求 | 无 PyMuPDF 部署下图片模板完全不可用：上传即 `engine_unavailable`。但图片模板是纯像素工作，不需要 PDF | §2.1/2.2 依赖耦合排查 | `templates_available()` 是**单一全局开关**（`ENGINE == "pymupdf"`），同时关掉 PDF 模板和图片模板；`image_template.py` 在模块级 `import pymupdf` | 图片模板被实现成「把图片包成一页 PDF → 跑 PDF 机制 → 再栅格化」，于是继承了 PDF 引擎的依赖；能力判定没有按格式区分 | ①能力按格式拆分：`pdf_templates_available()` / `image_templates_available()`（后者只要求一个可绘中文的字体文件）；②`image_template` 改为惰性导入 pymupdf，包装 PDF 在无 PyMuPDF 时用 Pillow 生成；③新增 `image_raster.py` 纯像素填充（背景重建清除 + `raster_text` 排版 + 原 DPI/ICC 编码）；④新增 `image_add_slot` 纯像素定义字段；⑤`pdf_slots_with_nrect` 对图片模板改用纯算术；⑥能力矩阵按格式如实标注 | **在 `import pymupdf` 被真实拦截的环境**跑通完整流程：上传模板 → 定义字段 → 每行一份 → 生成 → 任务 `completed` 2/2；输出 1190×900 PNG、**DPI 150.0124 保留**、**字段外采样像素零差异**（PNG 无损）、字段内写入中文「测试客户甲」且沿用原墨色。`tests/test_image_raster.py` 4 项 + `tests/test_raster_text.py` 16 项（与 PyMuPDF `insert_textbox` 的 `rc` 误差 ≤0.01）。全量 **233 passed** | 已修复（重复明细区域仍需 PyMuPDF，见说明） |
| DW-057 | P1 | 版式/内容完整性 | `fit` 只按页数验收：缩放把内容挤出固定高度行或文本框时，页数「达标」但交付的文档内容已经残缺；报告的缩放比例是请求值而非实际值；页数量自带字段标记的中间件而非真正交付的字节；样本失败时版式报告被丢弃——恰恰是用户最需要它的时候；`SHRINK_STEPS` 的 0.6 下限覆盖了字段自己的 `shrink_min` | §3 版式探查 | `layout.py` `fit_docx` 仅以 `p <= tpl_pages` 接受候选；`report["factor"]` 记录 `SHRINK_STEPS` 步进值；测量 `_strip_marks(trial, False)`（保留 sdt）而交付 `_strip_marks(data, not keep_fields)`（展开 sdt）；`jobs.py` 失败分支不带 `lay`；16 磅字段配 `shrink_min=4` 本可缩到 0.25，循环却停在 0.6 | 验收条件只有页数；缩放比例与测量对象都取错了；失败路径未保留诊断 | ①每个候选按**交付字节**计页；②`scale_fields` 返回实际生效比例/最小磅值/触底字段，报告改用 `effective_factor`；③新增逐字段文本比对，内容消失或被截断即判 `content_lost`/`content_truncated` 失败（不因页数达标而放行）；④失败时附 `layout_report`（模板页数、实际页数、实际缩放、最小磅值、触底字段、排版次数）；⑤缩放阶梯按字段自身 `font_size`/`shrink_min` 下延（未声明字号的 DOCX 文本字段沿用原阶梯，由每个 run 的下限兜底） | `tests/test_layout.py` 9 项通过，含「报告页数必须量自交付字节」（用 spy 断言首次计页的字节 == 返回的字节）与「被下限挡住时不得报告成已缩小」；全量 **233 passed** | 已修复 |
| DW-058 | **P0** | 迁移/升级 | 已经是 v2 的数据库**永远收不到 v3 的新列**，升级后应用运行时直接报 `no such column: origin`。已部署到生产的实例正是 v2 库，这个缺陷会在下次升级时打挂它 | 在**真实数据副本**上演练 v2→v3 迁移时发现 | 复制 `data/`（69 项目 / 80 模板 / 1675 产物）后构造 Store：schema 停在 2、`added columns: []`、随后查询 `origin` 抛 `no such column` | `_migrate` 的闸门写成 `if ver < 2`，只覆盖 v1→v2 这一跳；新增的 v3 列挂在同一个循环里，对 v2 库不执行 | 闸门改为 `if ver < SCHEMA_VERSION`。所有迁移都是加法式（`CREATE TABLE IF NOT EXISTS` + `PRAGMA` 守卫的 `ADD COLUMN`），因此对任何旧版本整体重放既安全又必要 | 真实数据副本实测：v2→v3 耗时 1 ms，8 张表行数**完全一致**，新增 `origin`/`archived_at`，69 行旧数据 `origin` 全为 NULL（未知来源，**不会**被当成测试数据），重复执行幂等。`tests/test_migration_v3.py` 4 项，恢复旧闸门后失败（已验证） | 已修复 |
| DW-059 | P2 | 性能 | 项目列表随项目数线性增加数据库往返：68 个项目要 137 次 SQLite 查询 | §6.2 首页探查 | `list_projects` 在 Python 循环里对每个项目各做一次 `COUNT(*)` 和一次「最近任务」查询 | 典型 N+1 | 改为固定 4 次查询：项目、来源数 `GROUP BY`、最近任务（`ROW_NUMBER() OVER (PARTITION BY ...)`）、模板名；顺带补上首页需要的 `template_name` | 真实 68 项目数据集实测：**137 → 4 次往返**，6.5 ms | 已修复 |
| DW-060 | P2 | 界面/操作 | 项目首页是一面卡片墙：68 个项目铺满整页，没有搜索、排序、归档，**删除是每张卡片上最显眼的常驻按钮**，而唯一的"让它消失"的手段就是删除 | §6.2 首页体验探查 | 68 个项目全部渲染为等大卡片；卡片上只有「重命名」「删除」两个按钮 | 没有归档能力（`projects.deleted` 已被软删除占用），列表接口也不返回模板名 | schema v3 的 `archived_at` 提供可逆归档；首页改为密集表格（名称/模板/来源数/样本规则/最近任务/更新时间），加搜索、三种排序、「显示已归档」；次要操作收进 `⋯` 菜单，删除不再常驻；删除确认里主动建议改用归档 | 浏览器实测真实 68 个项目：搜索「压力测试」68→1 且页脚显示「显示 1 / 68」；从 `⋯` 菜单归档后默认列表 68→67、`?archived=1` 仍为 68、Toast 说明如何撤销，随后已还原（0 归档）；表体可聚焦，↑↓ 三次落到第 3 行。`tests/test_project_archive.py` 5 项（归档不等于删除：归档后仍 200，删除后 404）。三套 E2E 通过 | 已修复 |
| DW-061 | P1 | 测试污染 | 68 个测试项目堆在用户的真实项目空间里，但**没有任何可依据的清理手段**：`manage.py` 没有项目清单/清理命令，而按名称前缀删除会误删用户自己改过的项目 | §6.1 | 真实库 68 个项目中 63 个在 v3 之前创建、`origin` 为 NULL，无法证明是测试数据；其中若干测试命名的项目在脚本运行数小时后被用户手工修改过 | 项目表此前没有来源字段（v3 才加），也没有任何清理工具 | `manage.py projects list/prune`：按 `origin` 证据分类（`test:` 可自动处理 / `ui`、被人工改过 → 保留 / 无来源 → **unknown，永不自动选中**）；dry-run 为默认；默认归档而非删除；删除额外要求 `--i-understand-delete-is-permanent`；`--id` 允许人工明确指定 | 在**真实数据副本**上实测：68 个项目判定为「可自动处理 0、明确保留 5、来源不明 63」，`prune` 不选中任何项目并提示只能人工指定；`--id` 默认 dry-run；`--delete` 缺确认标志时拒绝；`--apply` 归档后默认列表 68→67、含归档仍 68。**真实数据目录全程未被改动（68 / 0 归档）**。`tests/test_cleanup_tool.py` 6 项 | 已修复（blob 回收仍待做） |

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

验证：`.venv/bin/python -m pytest apps/docs/tests -q` → **154 passed, 1 skipped**（其中 `apps/docs/tests/test_job_reliability.py` 10 项针对上述语义）。

### 2026-09-23 自动化接口与文档（DW-010、DW-012、DW-030 … DW-032）

- `/api/v1`（39 条路由）：能力查询、文件、模板与字段、规则与映射、声明式定义导入导出、预检、任务（提交/查询/事件/取消/重试）、产物与打包下载、`report.csv`、临时下载链接。UI 与自动化共用同一套引擎实现。
- 鉴权：API 密钥 `dw_<id>_<secret>`（仅存哈希）、租户隔离、`read`/`write`/`admin` 权限范围、过期与吊销、令牌桶限流；界面访问模式 `none`/`basic`/`proxy`，非本机监听且为 `none` 时拒绝启动。密钥只能用 `python -m synwevo_docs.cli apikey` 管理，产品界面不暴露任何入口。
- 事件与 Webhook：`job_events` 稳定事件结构（事件 ID/类型/版本/任务 ID/发生时间/状态），签名 Webhook（HMAC-SHA256、时间戳、退避重试、投递记录、SSRF 允许列表），默认关闭。
- 健康检查：`/healthz`（进程存活）与 `/readyz`（存储可写、运行线程存活），不返回敏感诊断。
- 文档：`docs/integration/ports.md`、`api.md`、`automation.md`、`openapi.yaml`（由实现导出）、`docs/operations.md`、`.env.example`、`scripts/automation_example.py`。

验证：`apps/docs/tests/test_api_v1.py`（9）、`test_job_reliability.py`（10）、`test_webhooks.py`（5）、`test_api_docs.py`（3）；以及对运行中的服务按 `docs/integration/automation.md` 实跑一遍无界面流程（上传 → 模板 → 规则 → 映射 → 预检 5 条有效 → 提交 → 5 份产物 → ZIP 185 190 字节，内容逐字段核对正确）。

### 2026-09-23 界面、操作与性能（DW-040 … DW-046）

见上表各行的验证证据。本轮界面改动全部在 `frontend/src` 内，未新增任何 API/密钥/Webhook/端口相关的界面入口。

实测环境（所有界面与接口数字均来自这台机器）：Apple arm64 / macOS Darwin 27.0.0 / 15 核 / Python 3.14.5；浏览器 Chrome 152，窗口 1280×800；服务为 `backend/run.py` 单进程（127.0.0.1:8765）；输入为合成测试文件。

| 指标 | 方法 | 结果 |
|---|---|---|
| 首屏可操作（100 行 × 5 列项目） | `PerformanceNavigationTiming` + 接口耗时 | DOMContentLoaded 32 ms；项目/视图/定位接口各 2–11 ms |
| 打开 20000×30（3.5 MB）表格 | 同上 | 上传+解析 6.63 s（服务端）；首个网格窗口 380 ms；其后每页 11–12 ms |
| 单元格选区响应 | 页面内实测：事件派发 → React 提交 → 强制样式/布局 | 中位 5.9 ms，p95 6.7 ms（n=25） |
| 映射列表键盘移动（联动双侧高亮） | 同上 | 中位 5.1 ms，p95 5.9 ms（n=25） |
| 大表虚拟滚动帧间隔 | 可见窗口真实帧计时（`apps/docs/scripts/ui_perf.py`） | 中位 8.3 ms，p95 9.7，最大 16.6（显示器空闲节拍中位 8.3 ms，即跟随刷新节奏；仅渲染视口内约 200 个单元格节点） |
| 校验 / 样本 / 预览 / 定位接口 | 浏览器 `fetch` 计时 | 8 / 4 / 29 / 2 ms |
| 页面内存 | `performance.memory` | 打开大表后 7–10 MB |
| 批量生成 | `apps/docs/scripts/benchmark.py`（真实处理链路） | 100 份 DOCX 1.21 s（12.1 ms/份）；1 000 份 11.12 s；5 000 份 65.35 s（13.1 ms/份，峰值 RSS 452 MB）；PDF 模板 300 份 22.97 s（76.6 ms/份）；DOCX+PDF 双格式 20 样本 14.2 s（355 ms/份，LibreOffice 转换为主） |

说明：选区与键盘响应为**页面内测量**（事件处理 + React 提交 + 强制样式/布局），不含操作系统输入延迟；帧间隔另用可见窗口的真实帧计时测量（`apps/docs/scripts/ui_perf.py`，完整数据见 `verification.md` 第 11 节）。约 100 ms 的交互反馈目标与「滚动拖拽接近刷新节奏」在上述已测项目上达成，但这是本机（Apple Silicon、约 120 Hz）实测值，不代表其他机器、刷新率与文件规模。

---

## 平台单仓库重组轮发现并修复的问题（2026-09-24）

这些都是**重组本身**暴露或造成的，逐条实测确认修复。

| 编号 | 级别 | 类别 | 现象 | 怎么发现的 | 原因 | 修复 | 验证 |
|---|---|---|---|---|---|---|---|
| PR-001 | P1 | 打包 | 三个外壳的静态资源都叫 `/assets`，单进程部署里后挂载的那个会被前一个遮盖 | 拆成三个独立 Vite 构建后，`role=all` 同时提供工作台与账户外壳 | 一个构建改成三个构建，但 `assetsDir` 还都是默认值 | 各自取 `assets` / `account-assets` / `portal-assets` | `tests/integration/test_shells.py`：每个角色在 `/`、`/index.html`、`/portal.html`、`/accounts.html` 上都只返回自己的外壳 |
| PR-002 | P1 | 路由 | `role=app` 上 `/login`、`/account` 变成工作台 SPA 的「页面不存在」 | 把账户页收归账户服务后，依赖方不再打包这些路由 | 客户端原本靠 JS 跳转；服务端没有对应处理 | `synwevo_auth_client.build_account_redirect_router()`：服务端 303，`/login` 经 `/auth/start`（只有它会建可校验的请求行） | 实测 `role=app` 的 `/login` → 303 `/auth/start…`，`/account` → 303 账户服务 |
| PR-003 | P1 | 配置 | 角色由每次请求读环境变量决定，而路由改成按角色条件挂载后两者可能不一致 | 集成套件整体运行时，先建的应用把进程级角色改了，后面 20 多个用例连带失败 | 用模块级全局存角色，建一个应用就改变了后面所有应用的行为 | 角色随应用走（`api.state.role`），网关闭包捕获构建时的角色 | 全量套件从 35 failed 回到 0 failed；角色边界改为按角色各建一个应用来验证（`tests/integration/test_role_boundaries.py`） |
| PR-004 | P1 | 数据 | `Path(__file__).parents[N]` 在模块搬家后指向了错的目录：工作台构建产物找不到、回归用例目录被创建到 `apps/regression` | 起服务后所有页面 404；`list_cases()` 返回 0 | 数了固定层数的 `..`，层数随目录深度变了 | 构建产物按各自模块位置解析；数据目录改为向上找工作区标记，找不到**报错而不是猜** | 三个 `web_dist()` 实测指向正确目录；`regression.ROOT` 指向 `apps/docs`；空目录启动退出码 2 |
| PR-005 | P2 | 许可 | AGPL §13 的源代码归档仍按旧布局收集，且会把 `*.egg-info` 一起打进去 | 重组后跑 `test_licensing` 与 `/api/source-code` | `INCLUDE` 是旧路径；排除规则用集合成员判断，看不到 `synwevo_core.egg-info` 这种整段名字 | 清单按"正在运行的程序"重写（含共享包、账户服务、组装层）；排除改为同时匹配后缀 | 归档 275 个条目，`data/`、`.sqlite3`、`node_modules`、`.venv`、`testdata/`、`.env`、`egg-info`、`dist/` **0 个泄漏** |
| PR-006 | P2 | 依赖 | `pyyaml` 是测试用到但没有任何清单声明的依赖，只是碰巧装在旧虚拟环境里 | 在新建的干净 `.venv` 上跑测试，`test_api_docs` 报 `No module named 'yaml'` | 旧环境里手装过，没进 `requirements-dev.txt` | 写进 `requirements-dev.txt` 并注明用途 | 干净检出 `setup` → 650 passed |
| PR-007 | P2 | 文档 | `docs/architecture/platform.md` §1 论证"不搬目录"，而目录已经搬了 | 通读文档 | 上一轮的决定被本轮取代 | 该节改写为"已被后一轮取代"，保留原有理由并指向重组记录与新结构 | — |

