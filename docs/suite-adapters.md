# WP7：Docs / Message 注册表兼容适配器

现已接入 `synwevo_server` 的 portal/all 工厂，在 Suite 业务应用启用时自动准备并注册。
`all` 借用同进程 Docs/Message；portal 私有 Docs 服务复用原引擎和租约机制，解析读取器同时注入 Extract。
server 已声明与锁文件一致的 pydantic 直接依赖。下文保留适配与恢复契约。

## 已实现的范围

复用真实 Docs `App`、`JobService`、版本快照、逐对象授权、产物及原有下载响应；
复用 Message `Messaging` 的会话/组织成员检查、发送、持久 `client_id` 去重及所属进程的实时扇出。
没有创建另一套文档引擎、消息存储、身份或迁移，没有调用管理 API、铸造管理员凭据或发送外部邮件。

应用标识直接导入 `synwevo_core.apps`：`DOCS_APP_ID = "docweave"` 是稳定 app_id，
`MESSAGES_APP_ID = "messages"` 是实际 app_id；不注册新资格、不替用户启用应用。

以下表格中的操作 ID 为对应 **app_id + 后缀**，版本均为 1，输入/输出 schema 由注册表公开。
输入拒绝未知字段，ID 有格式限制；列表每页最多 100 条，明确返回 `next_offset`。

| 应用 | 后缀 | 行为 / 输入 | 权限与副作用 |
|---|---|---|---|
| Docs | `.project.list` | `q?`, `include_archived?`, `offset?`, `limit?` | 当前空间 read；真实服务分页 |
| Docs | `.project.get` | `project_id` → 模板版本、输入引用、`configuration` 指纹 | 当前空间 read；不解析、不渲染 |
| Docs | `.job.create` | `project_id`, `configuration`, `formats`；可选 `error_policy/layout/naming` | write；Agent confirm；必须有幂等键；async |
| Docs | `.job.get` | `job_id` → 实际 Docs 状态及统一 `state` | read；不推进任务 |
| Docs | `.job.artifacts` | `job_id`, `offset?`, `limit?` → 可用产物元数据 | read；拒绝已撤回输入/不可读 blob |
| Docs | `.artifact.get` | `artifact_id` → 元数据及需登录的 `download_url` | read + 原有 export 权限 |
| Message | `.conversation.list` | `offset?`, `limit?` → 本人授权的当前空间群聊 | read；不读取群消息正文 |
| Message | `.conversation.get` | `conversation_id` → 名称、成员、`audience` 指纹 | read；逐对象复验 |
| Message | `.message.send` | `conversation_id`, `audience`, `body?`, `artifact_ids?` | write；Agent confirm；必须有幂等键；external |
| Message | `.message.inspect` | `run_id` → `delivery=recorded/unknown`, 消息 ID/序号 | read；只查询原发送者自己的运行 |

首次明确限定 Message 为**绑定该目标工作空间的群聊**。原产品的组织级私聊和未绑定空间的群聊仍由
原 Message 使用；不把它们伪装成空间业务对象。本适配器的 write 门槛比原 Message 成员发送权限更窄。
不能靠在空间 A 有资格访问空间 B 的会话。平台管理员没有成员资格时同样拒绝。
Docs 对象讨论群额外验证当前 Docs 应用资格和项目可见性，不依赖桥接器尚未完成的成员清理；
其他类型的对象讨论暂不支持自动发送。

发送确认携带完整会话 ID、当前受众指纹、正文与产物 ID。成员变化或归档使旧受众指纹失效。
发送不创建/加入会话、不扩大受众、不支持群体提及或任意连接器。
最多十个产物，且必须属于同一目标空间；逐个验证发送者的 Docs 读取与导出权限。
跨空间成果转移应先走既有合法复制/审批机制，取得目标空间产物后再发送。

## 版本、产物与撤权

`project.get` 的 `configuration` 包含项目设置、映射、实际选定模板版本和输入版本/内容引用。
提交时在写事务内重新计算；内容变化返回 `configuration_changed`，要求重新读取并确认。
真正的作业快照仍由 `JobService.create` 生成：模板版本、映射修订、源文件、时钟都由原引擎固定。
不会修改原项目或临时覆盖映射来塞入模型返回值。

所有后台任务的 principal 都从本次真实 user ID 与显式空间重新生成；
即使内部调用方传入 `operator`，也不会把它作为 Docs 执行授权。
Docs 原 worker 在取任务、样本处理和产物发布前复核该用户当前权限。
普通用户无法通过注册表接触平台管理接口。

下载路径：`/api/suite/{DOCS_APP_ID}/workspaces/{workspace_id}/artifacts/{artifact_id}`。
空间在路径中固定，不从当前浏览器标签页或 session 的可变空间猜测。
它只使用真实会话与当前资格，每次下载再次验证项目、job、产物、隔离状态和导出权。
这是**需登录的链接**，不签发 bearer 下载 token，不转移访问权；会话成员没有 Docs 权限仍不能下载。
`artifact.get` 只读取元数据和构造固定路径，没有签名、blob grant 或业务写入。

所有 mutation 都有 `Operation.result_guard`。重放/历史展示重新验证 job 或消息的当前 ACL；
消息回执还会复验其产物来源。退群、撤销应用、删除项目/产物、撤回消息不能借旧回执读取受限结果。
所有注册的读操作均可经 `caps.inspect` 运行，并且只读；测试用连接的 `total_changes` 验证关键读取没有写入。

## 幂等、状态和不确定结果

调用 `.job.create` / `.message.send` 必须提供注册表 `idempotency_key`。
同一用户、操作、空间、键绑定同一份清理后的参数；变化返回冲突。
注册表生成的持久 `run_id` 同时作为 Docs 幂等键 / Message `client_id`，调用方不能覆盖它。
去重不依赖内存或文件哈希；不同合法业务请求使用不同键，可以生成同样内容。
不要清理尚需恢复的 `op_runs`，否则会丢失业务请求与原 run ID 的绑定。

Docs 作业创建与 `op_runs`、提交事件共用**同一个 Store 对象的嵌套事务**，提交只产生一个 job。
作业由 Docs 自己运行；创建返回 `queued`，不会把已受理当成生成成功。
`completed → succeeded`，`completed_with_errors → partial`，`cancelling → running`，其他状态保留。
`.job.get` 是作业当前状态的权威查询。
适配器的收据同步器每两秒分页查看持久任务，把状态投影到 `.job.create` 的 operation receipt；
它不渲染、不重跑，不由读取操作触发。关闭后台运行时须由测试/受控宿主显式调用 `sync_jobs()`。

Message 为两阶段调用：先持久记录运行，再调用现有业务服务。
权限、受众检查与本地消息写入在同一事务；复用所属 Message 实例的扇出，并延迟到提交之后发送实时事件。
成功只意味着真实消息记录已入库，**不表示收件人已读或外部网络已确认**。
消息与回执之间进程崩溃时，原运行可能保留 `running`，共享 stale reconciliation 将其变为 `unknown`。
适配器遇到结果不明确的调用异常也返回 `unknown`。

恢复顺序：

1. 保留原业务幂等键和 `run_id`，不换键盲发。
2. 调用 `.message.inspect`，读取 Message 中该 sender/conversation/client ID 对应的真实记录。
3. `recorded` 才能让流程据此继续；没有记录返回 `unknown`，不是“未发送”或“可以重试”。
4. 对账不发送、不重写原运行、不重新发布实时事件。原两阶段运行仍可能显示 unknown，流程应保存对账证据。
5. 原键重放永不再次发送。消息被删除/隐藏/撤回或本人退群时，不能通过对账恢复被隐藏的旧正文。

Message 操作完成事件可用于唤醒，但不能当作外部发送的唯一对账依据：业务提交与注册表回执之间存在
故障窗口。协调者的流程恢复必须使用上述持久消息记录；如需要重建事件，需要独立、有幂等保护的恢复契约，
不能通过 inspect 写库。此适配器不承诺任意外部系统的 exactly-once，也不接入外部邮件或客户消息。

## prepare / install 与 start_jobs

首选在 server composition root 组装好平台及原有应用服务后、**Suite 安装及任何兜底 SPA 路由之前**执行：

```python
from synwevo_server.suite_adapters import prepare
from synwevo_suite import app as suite_app

adapters = prepare(
    api, plat,
    docs_api=api if wants_app else None,
    messages_api=api if wants_messages else None,
    start_jobs=start_jobs,
    public_url=workplace_origin,  # 可信配置的真实 Suite origin，不能取任意请求 Host
)
ctx = suite_app.install(api, plat, docs=adapters, start_jobs=start_jobs)
```

已有 SuiteContext 的显式替代是 `install(ctx, adapters)`，仍必须在兜底 SPA 路由之前。
同一个 ctx 重复 register 是 no-op；不允许把一个 prepared adapter 安装到多个 ctx，
也不允许一个宿主重复 prepare 或拼接不同 Store 实例。不能为了接入再调用一遍 suite.install 覆盖已有注册表。
Agent 使用同一个可变 registry，在首次服务请求前完成注册即可。

- 若 `docs_api` 已有真实业务实例和 JobService，借用它们，不另外启动第二个 Docs worker。
- 宿主没有 Docs 时，prepare 通过原 Docs installer 创建私有 ASGI 实例，只取业务服务，**不对外挂载其路由**。
  私有 Docs 的关闭 hooks 转交宿主，涵盖 worker、解析池、协作、copy runner 和 webhook dispatcher。
- 原工厂在 `start_jobs=False` 时 `state.jobs=None`；prepare 会预先创建一个 `JobService(start=False)`。
  其构造函数会做原有租约恢复，所以只能在 prepare 阶段构造，不能在 inspect 中懒构造。
- `start_jobs=True`：新建的 Docs worker 使用原恢复/租约机制；Suite register 启动状态投影 ticker。
  宿主 lifespan 关闭时停止；已有 Docs 实例的生命周期仍由其所属工厂管理。
- `start_jobs=False`：不启动 Docs 生成线程、收据 ticker 或新 Message bridge；测试显式驱动原 JobService。
  参数应与 Suite、宿主一致；不会停止宿主此前已经自行启动的 worker。
- `messages_api` 只接受当前进程真正承载 Message 的实例，并且必须与 Suite 使用相同 Store 对象。
  缺失时（生产 portal 角色）只用核心 `Messaging` 做同规则的只读核对；**不新建私有 Message 写服务/事件 hub**。
- `messages.message.send`（2026-10-03 起为 async）：核对会话、受众指纹、产物导出权与发送频率后，
  在同一事务里写入 operation 收据与 `messaging_outbox`（kind `user.message`，event_id=run id）。
  Message 自己的进程（`BridgeWorker`）以本人身份投递：投递前再次核对本人访问、对象讨论授权、归档状态、
  完整受众指纹与账户状态，以 run id 作为 client_id，崩溃重投不会产生第二条；明确拒绝记录为
  archived / audience_changed / no_conversation / account_unavailable 等，超出尝试上限为 delivery_failed。
  Suite 的收据 ticker（`sync_messages`）只按真实结果把收据更新为 succeeded（含 message_id，并写入
  `messages.message.recorded` 事件）或 failed；从不发送、不重排队。
- `public_url` 为空仍可读取相对下载路径，但带产物的 Message 发送明确返回未配置；生产须配置真实 Suite origin。
- prepare/register 不调用应用 bootstrap，不给任何用户/空间授权；原应用部署仍负责稳定 app_id 注册。

`all` 与 `portal` 角色使用同一条发送路径：发送只排队，由 Message 进程写入与扇出，因此跨进程
（workplace 源与 message 源分属不同容器、共享同一数据库）的实时推送、未读与摘要与在 Message 内发送一致。
测试以 `bridge_worker.run_once()` 与 `sync_messages()` 显式驱动；隔离与故障用例见
`tests/integration/test_suite_adapters.py`。生产线上跨进程投递尚未做线上复查。

## 上游及协调者需要提供的契约

1. **依赖声明**：server 已声明直接 `pydantic` 依赖，与现有锁版本一致，边界检查通过。
2. **服务组装**：已在 server `app.py` 完成，向 `suite.install(docs=...)` 注入。
3. **输入物化**：首批仅接受已配置 Docs 项目和已解析输入。Forms/Extract/DataPrep/Tables 的输出需通过
   现有 Docs 上传/项目定义服务物化为目标空间的输入，并固定模板、列映射及来源版本。
   不接受任意 blob hash、服务器路径、SQL、表达式执行或覆盖成熟项目的临时参数。
   一个业务运行应使用自己的项目/输入或上游已有的不可变输入，避免多个流程共改一份活动来源。
4. **审批/流程**：上游消费真实通过决定后才调用生成；保存 source references、`configuration`、
   业务幂等键、operation run ID、Docs job ID。等待/失败/partial 时按策略停止，不发送假成果。
5. **Agent**：确认层绑定确切调用、空间、受众指纹及来源；mutation 的 `agent=confirm` 是注册声明，
   确认的存储/消费仍由已有 Agent Tools 层负责，不把 `caps.invoke(via="agent")` 本身当成确认令牌。
6. **通知**：本切片写入真实群消息并复用实时 hub，没有调用原 HTTP 路由内私有的收件箱回调；
   如需完全一致的附加 inbox 通知，应由 Message 暴露统一 send-and-notify 服务后接入，不能复制私有业务代码。
   不新增邮件任务、不修改私信邮件默认关闭设置，也不把每条消息转成邮件。
7. **浏览器及全链路**：由协调者验证部署后的真实 origin、点击下载、群消息界面、其他应用中间步骤与模型。
   本切片无需真实模型即可验证业务服务，但不因此宣称完整 B1 或真实模型验收通过。

## 原子任务历史验证记录（集成前）

使用主 checkout 的 `.venv/bin/python`，所有模块由工作树显式 PYTHONPATH 提供。
测试数据仅在 pytest 临时目录，用户均为 `example.test` 夹具，邮件落隔离文件目录；无真实客户消息。

```sh
# 在本工作树根目录运行；主解释器路径由调用者设置，不能用工作树外的模块替代。
export PYTHONPATH="$PWD/packages/core:$PWD/packages/auth-client/python:$PWD/apps/docs:$PWD/apps/messages:$PWD/apps/portal:$PWD/apps/suite:$PWD/services/identity:$PWD/infra/server"
"$MAIN_PYTHON" -m pytest tests/integration/test_suite_adapters.py
```

覆盖实际 Forms 提交 → 测试显式物化为 Docs XLSX 输入 → 固定版本真实 DOCX → 受控测试群链接。
其中物化为测试夹具的服务调用，不冒充已完成 Extract/DataPrep/Tables/Approvals 编排。
覆盖同键并发与参数冲突、重建真实应用实例后的对账、后台真实生成和收据同步、权限撤销导致 queued job 停止、
删除/撤权后的旧结果隐藏、跨空间对象和产物拒绝、viewer 导出/写入拒绝、无空间资格的管理员拒绝、
受众变化拒绝、未知发送无盲重试以及 inspect 无写入。

2026-09-30 最终验证（基线加本工作树三个未提交文件）：

```sh
"$MAIN_PYTHON" -m pytest tests/integration/test_suite_adapters.py \
  apps/suite/tests/test_capabilities.py apps/suite/tests/test_run_visibility.py \
  tests/integration/test_suite_mount.py tests/integration/test_messaging_bridge.py -q -o addopts=''
# 47 passed, 6 warnings in 22.60s
```

其中 WP7 新增 17 项，原有相关回归 30 项；无失败、无跳过。
六条 warning 来自现有 Starlette/AnyIO 与文档依赖的弃用提示。
真实 partial（一个成功、一个缺必填而跳过）及全失败任务也已验证，未被投影成 succeeded。

- 通过：`check_brand.py`、`check_design.py`、`check_component_names.py`、`brand_assets.py --check`（22 个 Logo）。
- 未通过：`check_boundaries.py`，仅 server 缺少直接 `pydantic` 依赖声明这一项（509 个 Python、266 个前端文件）。
  已在上面的协调者契约列出；跨应用导入方向检查没有其他问题。没有修改检查器。
- 通过：`git diff --check`；三个新增文件另逐行检查无尾随空白。
- 未运行：全仓测试、UI 类型/构建、浏览器、真实模型、CI、部署；均不宣称通过。
- Git：仅此三个授权文件新增，未提交、未推送、未部署。

## 协调集成验证

2026-09-30，8e8fcec 基线上集成 Flows/适配器后：suite 全部测试、真实 Docs/Message 适配、portal/all 挂载、真实 PDF 和消息桥专项 **270 passed, 0 skipped**，90.50s。日志 `/private/tmp/synwevo-wp7-integrated.log`，JUnit 同名xml。两项生产 Approvals 流程测试已实际执行通过，原基线的跳过不计验收。
这验证真实 Forms→独立 Approvals→受控业务操作、等待期间 Store 重建、重复事件不重复动作，以及真实 Docs→消息写入各段；完整七应用 B1 尚需数据桥接，不能拼接分段结果冒充端到端完成。
