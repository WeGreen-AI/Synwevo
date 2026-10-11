# Synwevo API 平台：能力覆盖表

唯一的 API 能力地图（API 平台专项，约定见 [`../ai/api-platform.md`](../ai/api-platform.md)）。管理员平台的整体覆盖表是
[`admin-coverage.md`](admin-coverage.md)；两者重叠的「API 与集成」一栏以本表为准。

状态词：**已验证**（有测试或实跑证据，列出）· **部分** · **需实现** · **受阻**（外部条件）· **不适用**（说明理由）。
「对外」= 外部系统可用机器凭据调用；「管理」= 平台管理员 `api_platform.manage`；「仅内部」= 只有浏览器会话。
建立于 2026-09-29（三路代码走读 + 实跑），逐条以代码为准；旧记录里的「完成」是线索，不是证据。

**2026-09-30 本批更新：目录写入、复制授权与执行恢复、任务响应契约及 SDK 已完成本地验证，尚未部署。**
本页的「已验证」不代表 CI 通过或正式环境已经发布。Docs 当前导出为 82 个路径、100 个操作（包含健康检查）；
Python SDK 映射 98 个、CLI 可达 64 个，本轮联合实跑成功观测 56 个。目录单独为 7 个路径、8 个操作（3 读、5 写），
Python 与 TypeScript 的独立 `WorkspaceClient` 均实现并实跑 8/8。方法存在、某次调用成功与完整参数/故障覆盖是不同口径，
详细证据与未覆盖项见 §7。

## 1. 身份、凭据与授权

| 能力 | 状态 | 实现与证据 | 缺口 / 下一步 |
|---|---|---|---|
| 三类机器主体：旧 `dw_` 密钥、服务账户（`client_credentials`）、用户委托（授权码） | 已验证 | `packages/core/synwevo_core/machine.py`；`test_machine_identity.py`、`test_credentials_*` | 委托客户端不在工作空间审批模型内 |
| 所有者审批 → 管理员签发 → 每次调用复核审批（范围、到期、审批人仍是所有者） | 已验证 | `api_platform.py` `Approvals`/`enforce_principal`；`test_api_platform*.py`（39 项） | 依赖**具体审批人**：该所有者降级即全部失效（其他所有者仍在）；无职责分离检查 |
| 秘密只显示一次、服务端只存哈希 | 已验证 | `auth.py`、`oidc.py`（SHA-256）；`Secret` 对话框 `no-store` | 无 pepper；无末四位展示 |
| 轮换重叠期（≤24 h）、撤销、停用/启用（服务账户） | 已验证 | `auth.rotate_key`、`oidc.rotate_secret`；**API-1**：控制台可选 0/10 分钟/1 小时/24 小时重叠（此前固定 0） | 访问密钥无「暂停」（停用 = 永久撤销）；审批到期后无续期，只能新审批 |
| 最近使用 | 部分 | **API-1**：凭据列表显示密钥 `last_used_at`、服务账户最近调用时间（30 天调用记录） | 委托令牌无 |
| 撤权传播 | 已验证 | 每次请求查库（无缓存）；延后工作经 `authority.check` 复核 | 无跨进程事件总线（目前不需要：无缓存） |
| OAuth 授权码 + PKCE（S256 强制）、刷新轮换与重放检测、RFC 7009 撤销、精确回调 | 已验证 | `provider_routes.py`、`oidc.py` | — |
| 第三方客户端同意页、已连接应用管理 | 已验证 | **API-4** 同意暂存与决定、「已授权的应用」撤回即撤令牌、撤回写安全日志、旧委托记为旧授权；`test_oauth_consent.py`（10）；隔离实例浏览器实跑（同意页 → 批准 → 令牌 200 → 撤回 → 401） | 组织层面的管理员代同意不做（同意属于本人） |
| 令牌自省（RFC 7662）、`client_secret_basic` | 已验证 | **API-4** `/oidc/introspect`（只答自己的令牌）、令牌/撤销端点接受 Basic；发现文档已登记 | `private_key_jwt`、mTLS、DPoP 未做 |
| **平台管理机器身份**（管理 scope、管理 API 可程序化调用） | 需实现 | `/api/admin/*` 只认会话 Cookie；机器凭据现有三个资源（Docs、工作空间目录、业务应用操作；后者只接受用户委托，不走所有者审批） | 批次 API-3，依赖管理员平台 B1-2 委派权限 |

## 2. 契约、错误与追踪

| 能力 | 状态 | 实现与证据 | 缺口 |
|---|---|---|---|
| Docs 与目录各自从实际路由导出 OpenAPI 3.1，管理台组合两份契约 | 已验证（本地） | [Docs 契约](../integration/openapi.yaml)、[目录契约](../integration/workspace-openapi.json)；`synwevo_docs.cli openapi`、`synwevo_identity.cli workspace-openapi`；`test_api_docs.py`、`test_workspace_contract.py`。复制 6 操作与目录 8 操作有显式请求/响应模型，任务生命周期 9 操作及断点上传 5 操作补齐响应模型；保留历史 null、缺省字段和不同产物清单形状。上传冲突/过期/超限/范围缺口的 409/410/413/416 已登记，两种错误媒体格式均按实际响应验证。来源未就绪/隔离/内容缺失、预览冲突和签名下载失效已补齐契约；专项观测 222 个错误响应、77 组操作/状态均符合 schema。目录导出不打开数据库，独立账户服务不依赖 Docs；组合仅保留业务路径与可达 schema | Docs 当前仍有 **59 条 2xx JSON 响应 schema 未约束**（全量扫描当前导出），不能称全部响应已类型化；管理 API 不在对外业务契约里 |
| 版本与弃用规则 | 部分（本地） | [版本说明与弃用清单](../integration/api.md#deprecations) 区分 HTTP、契约、事件、SDK 与部署版本；记录现有任务正文幂等别名、替代请求头、优先级、Deprecation 头及未设 Sunset 的事实 | 管理台兼容差异视图、按客户端统计旧字段使用仍未实现 |
| 错误信封 + 可选 RFC 9457（`Accept: application/problem+json`） | 已验证 | `http.py`；`test_gate_api_errors.py` | 类型 URN 不可解引用 |
| `X-Request-ID` 全链路回显、调用记录 | 部分 | `http.py` 网关；**API-1**：调用记录带错误代码与客户端，可按请求编号/凭据/接口/状态/错误代码查询；网关拒绝的请求记在接口模板下 | **API-2**：提交任务的请求编号记在任务 `request_id` 与 `job.created`（`test_request_trace.py`）；无 traceparent |
| 分页：offset 与资源列表游标 | 已验证（本地） | Docs 8 类资源列表使用 24 小时签名游标，绑定空间、列表、对象/筛选和组织；每次仍重新鉴权，当前有效的后继凭据可续读，独立进程可读同一游标。`test_api_contract_pagination.py`、`test_org_templates_v1.py`；已安装 SDK 的任务分页实跑通过 | 移动视图，不承诺冻结快照；升级/回滚时旧格式需从头同步。`/events` 与 Webhook 投递游标仍是各自既有格式，本批未更改其长期消费语义 |

## 3. 流量、配额与用量

| 能力 | 状态 | 实现与证据 | 缺口 |
|---|---|---|---|
| 每凭据请求速率（跨进程共享） | 已验证 | **API-1** `api_quotas.RateGate`（GCRA，整数纳秒，库内状态）；`test_every_process_shares_one_budget`、`test_the_arithmetic_admits_exactly_the_budget_at_any_size` | — |
| 每工作空间合计请求速率 | 已验证 | **API-1**；`test_a_workspace_budget_holds_across_its_credentials_and_nowhere_else`；隔离实例实跑两把密钥第 4 次 429 | — |
| 轮换不换额度（按密钥谱系计） | 已验证 | `test_rotation_does_not_buy_a_fresh_credential_budget` | — |
| 同时进行的生成任务（网页与 API 同一上限） | 已验证 | `jobs._admit` 读工作空间策略；`test_a_workspace_policy_replaces_the_deployment_limit` | — |
| 管理员编辑（平台/工作空间/凭据层，继承、版本、预演、历史、审计） | 已验证 | **API-1** `/api/admin/api-platform/quotas*`；控制台「流量与配额」；隔离实例浏览器实跑（预演 → 重新验证 → 保存 → 实际 429）；**API-6** 管理表格已有手机卡片布局 | — |
| 存储、页数、生成额度、用量账本 | 需实现 | 只有实时队列 `/usage` | 没有计费业务：只做用量与额度，不编造价格 |

## 4. Docs 业务 API（`/api/v1`）

| 能力 | 状态 | 实现与证据 | 缺口 |
|---|---|---|---|
| 项目、来源、续传上传、模板（上传/内置编辑器）、规则、映射、定义、预检、预览、任务、产物、下载链接、ZIP、报告 | 已验证（本地续验） | `test_api_platform_pipeline.py`；本轮 Python SDK/CLI 读回 24 份上传模板 DOCX、48 份内置 DOCX/XLSX 及 7 份预览；TypeScript 实跑另见 §7。当前 Docs 导出共 100 操作，不能据此声称全部均已实跑 | 本轮 SDK/CLI 联合成功观测 56/100；其余逐项覆盖以审计清单为准 |
| 幂等（数据库唯一约束、同键冲突、重放复核） | 已验证 | 共用 `synwevo_core.idempotency`（Docs 兼容导入）、`jobs.py`；复制申请、组织模板发布/使用及目录邀请创建/重发沿用同事务机制，当前授权独立复核 | Webhook 创建/轮换/ping/重投拒收幂等键；恢复版本仍无持久幂等，依赖当前版本检查。外部安全日志与数据库不构成同一原子提交 |
| OCR 触发 | 已验证 | **API-2** `POST /api/v1/files/{fid}/ocr`（同一服务、未接纳 409、识别槽位 429 `ocr_busy`）；`test_machine_ocr_*`；SDK 实跑 | 同步执行（≤50 页）；超长识别未做成任务 |
| 上传模板的字段修改/删除 | 已验证 | **API-2** `PATCH`/`DELETE /templates/{tid}/fields/{field_id}`（`base_head` 条件更新）；`test_api_template_fields.py`；SDK 实跑 | 整份定义替换（按定义对账）未做 |
| 项目改名/归档/输出默认值 | 已验证 | **API-2** `PATCH /api/v1/projects/{pid}`（`base_settings_rev` 必填、同事务）；`test_api_project_settings.py`（4，含 6 路并发） | 规则试算（不保存）仍为浏览器专有 |
| 跨空间复制：申请、授权、执行与恢复 | 已验证（本地，未部署） | **API-5** 6 操作：目标列表、固定 manifest、申请列表、创建、详情、撤回；仅来源本人委托令牌。`requested_authority` 保留客户端/委托授权，发布事务内重验来源、目标、审批人及当前同意；副本、完成状态与最小 `copy.*` 事件同事务发布。`test_api_copy_requests.py`（18）：并发幂等、撤授权/停客户端、声明后撤权、发布故障回滚、旧执行声明失效、历史结果恢复及歧义拒绝；场景 C 覆盖目标另行授权生成、卡片不授予内容权限、事件不跨空间泄漏 | 仅个人空间 → 组织工作空间；审批留在网页并需近期重新验证，无机器审批端点。来源变化/失权变为 `stale`，需重新申请；历史多重或错目标痕迹保留并报错，需人工核对，不自动重做 |
| 评论/提及、活动 | 需实现 | 浏览器会话专有 | 批次 API-5b |
| 组织模板 | 部分 | 只接受委托令牌（成员身份属于人） | 设计如此；服务账户场景待定 |

## 5. 事件与 Webhook

| 能力 | 状态 | 实现与证据 | 缺口 |
|---|---|---|---|
| 事务 outbox（任务事件与状态同事务） | 已验证 | `jobs.py`、`webhooks.py` 调度游标 | 事件与投递表无保留期 |
| Standard Webhooks 签名、轮换重叠、SSRF（每次解析、连接已验证 IP、拒绝跳转） | 已验证 | `webhooks.py`；`test_webhook_*` | 只用第一个解析地址 |
| 重试、死信、人工重投、暂停/恢复 | 已验证 | `webhooks.py` | 暂停期间事件丢弃；无批量重放 |
| 事件目录 | 部分 | `job.*`、`webhook.ping`；**API-2** `file.ready/needs_ocr/rejected/failed`（与状态同事务；`test_file_events.py` 8 项）；**API-5** `copy.*` 与复制状态同事务，仅来源空间可见；**API-5** `member.added/changed/removed`、`invitation.created/accepted/revoked`（`synwevo_core.member_events` 触发器，与行变化同事务，仅标识；`tests/integration/test_member_events.py`）；**API-5** `template.created/updated/deleted/published/released/deprecated`（`synwevo_core.template_events`；`tests/integration/test_template_events.py`） | 本批成员写入的站内通知与审计不等于提供成员事件订阅。任务完成负载只给计数，产物需另用当前有效凭据读取，撤权即时生效 |
| 控制台跨工作空间 Webhook 视图 | 已验证 | **API-6** 投递运行概况（元数据、接收方只显示来源）与保护性暂停；总览事件积压；`test_api_platform_webhook_ops.py`（3）；隔离实例浏览器实跑 | 平台侧重投不做（属于工作空间）；跨空间投递明细不显示负载 |

## 6. 其他模块的机器 API

| 模块 | 状态 | 实现与证据 | 缺口 |
|---|---|---|---|
| 工作空间目录：3 读 + 5 写 | 已验证（本地，未部署） | **API-5/5b** `/api/workspace/v1`：whoami、成员/邀请列表、邀请创建/重发/撤回、角色修改、成员移除；独立资源与受众，`workspace.read` / `workspace.members.write`，所有者批准的服务身份。`test_workspace_directory_api.py`、`test_workspace_directory_writes.py`；真实邮件 outbox → 本人接受 → 改角色 → 移除；并发幂等、原审批撤权、版本冲突、字段白名单、冻结/限额、三语言署名与治理审计已验证 | 仅 viewer/editor；admin/owner 必须本人操作，用户委托目录操作与应用资格写入未开放。安全日志先落盘后事务失败时，恢复可能保守撤权；降级旧代码前须处置未接受的机器邀请，见 [目录写入与恢复边界](api-directory-writes.md) |
| 业务应用操作（Forms、Tables、DataPrep、Extract、Approvals）：102 个操作（含组装层的 `extract.file.from_forms`）+ whoami / operations / runs / events | 已验证（本地，未部署）；**仅用户委托** | **WP9** `/api/suite/v1`，工作空间源站：独立资源 `urn:synwevo:platform:suite`，12 个 scope（各应用 read/write，`approvals.decide`、`extract.decide`）。每个操作一条路由，统一经 `Capabilities.invoke(via="api", scopes=…)`；scope 由操作声明推导（`machine_scope`），嵌套的跨应用读取同样受凭据 scope 约束；凭据只属于授权时选定的一个工作空间；幂等键按客户端命名空间；回执只对发起它的客户端可见。九条文件路由（Extract / DataPrep 上传与导出、Tables 导入上传与导出下载、Forms 附件）是各应用同一处理函数在机器前缀下的第二次挂载，接纳链、导出权与页面相同（`test_machine_files.py` 8 项，含三条无浏览器完整流程）。契约 [`suite-openapi.json`](../integration/suite-openapi.json) 由组装后部署的注册表生成（`python -m synwevo_server.suite_contract`）并有漂移测试；Python / TypeScript `SuiteClient` + `UserToken`（刷新轮换）。`apps/suite/tests/test_machine_api.py`（19）、`test_machine_contract.py`（3）、`test_machine_sdk.py`（2）、`tests/integration/test_suite_machine_flow.py`（真实同意与令牌端点）。说明见 [业务应用操作接口](../integration/suite-api.md) | **服务账户不支持**（各应用在 `invoke` 之外约十余处按用户行与成员关系重新授权，需主体感知的统一授权，另立工作包）；事件拉取（`GET /events`：53 个事件类型，仅路由信息，按工作空间、read scope 与逐记录可见性过滤，游标密封并绑定客户端/本人/工作空间，保留 30 天（游标另有 24 小时宽限），过期或与日志不一致 410，单次请求读取的行数有上限；`test_machine_events.py` 24 项）；**签名 Webhook 推送已实现**（本地，未部署：`/webhooks/subscriptions` 12 条路由，Standard Webhooks，投递内容 = 事件流条目且同 id；入队与每次发送前复核授权（须可续期：未撤销、刷新令牌未过期、登录未被结束、资格仍在）与逐记录可见性，授权结束即停用并清空队列；按序、队列上限 100、落后超过保留期即停、停止处给出补读游标；地址每次发送前复核并固定连接；密钥只显示一次并密封，纳入根密钥更换；暂停/改地址/换密钥/停用写入安全日志，恢复备份不复活；管理操作写入审计；`test_machine_webhooks.py` 81 项 + SDK 真实服务 1 项，87 项反向检查）。**已知缺口**：① 本人与空间管理员尚无“哪些地址在接收事件”的列表与停用入口（现以撤回授权停止推送）；② 接收地址不做握手验证，靠每授权/客户端/空间上限、每主机每分钟上限与 ping 限额约束；③ 不能按客户端登记允许的接收主机；④ 服务账户不能订阅；个人 Agent、计费与同意不在机器接口内（设计如此）。委托发起的延后工作（DataPrep 运行、Extract 任务、审批等待）在每个复核点按存储的描述符复核授权，授权结束即停且不再写入（`Capabilities.standing` / `resumed`，`test_machine_deferred.py` 26 项），与 Docs 一致 |
| 成员导入 | 需实现机器接口 | 已有异步任务 + 预检 + 确认 | 需组织级机器主体 |
| 通知发出、对象卡片 | 部分 | 复制生命周期对象卡片、邀请/成员写入通知来自真实业务事件 | 无供外部集成任意发通知/卡片的通用入口 |
| Message（会话、消息、机器人） | 需实现 | 现有会话接口 | 机器入口未开放；机器人身份需清楚标识 |
| 管理只读（审计、调用、配额） | 需实现机器接口 | 现有控制台仍要求有效平台管理员会话 | 批次 API-3 |

## 7. SDK

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| Python SDK 0.1.0（重试、幂等键、流式下载、续传、Webhook 验签） | 已验证（本地续验） | 本地重新安装的包：单元测试 27/27、安装包的 mypy 严格类型检查通过；任务生命周期公开 TypedDict 不改变字典运行行为。[操作覆盖清单](../../apps/docs/sdk/python/operation-coverage.json)：Docs 100 操作 / SDK 映射 98 / CLI 可达 64 / 前批联合成功观测 56；`tests/integration/sdk_acceptance/` 真实服务验收 | 未覆盖来源页 PNG 与 locator probe；CLI 为子集；未实跑操作不据此获证。SDK 单元测试已进入 CI，完整隔离服务验收仍由脚本运行；未公开发布包 |
| TypeScript SDK 0.1.0（`.mjs`、类型声明、独立目录客户端） | 已验证（本地续验） | 本地打包安装后 10/10 单元测试、严格类型检查通过；上传模板 SDK/CLI 各 12 份 DOCX，内置模板 12 DOCX + 12 XLSX 与 2 预览读回验证；来源检视、模板恢复、凭据与签名链接的方法及声明已存在；复制 6 操作、目录 8 操作均有真实 HTTP 验收 | Docs 尚无 TypeScript 独立逐项成功覆盖证明；不能把 Python/CLI 联合 56 操作计数说成每种语言均覆盖 56。同步抛出的 ValidationError 仍无法由类型系统表达；包未公开发布 |
| Python / TypeScript `WorkspaceClient` | 已验证（本地，未部署） | 独立于 Docs 客户端，使用账户源站及目录 resource，不继承 Docs 方法、默认 scope 或 API key 环境变量；两种已安装包分别实跑 8/8。r3 合计 36 次目录业务 HTTP、4 封真实 file-mail/outbox、10 条邀请/成员审计；`audit_workspace_openapi.py` 校验操作与方法映射 | 写权限须在所有者审批时明确授予；改角色/移除必须带 `expected_role`，不自动重试不确定结果。没有目录 CLI |
| 多策略 `RateLimit` 头解析 | 已验证 | **API-1** 两个 SDK 取 `r=0` 各项最长 `t` | — |

本轮实跑原始报告位于隔离临时目录：Python `synwevo-api-sdk-python-20260930-r3/acceptance.json`、
TypeScript `synwevo-api-sdk-ts-20260930/verification.json` 与 `builtin-verification.json`、
目录 `synwevo-workspace-sdk-20260930-r3/acceptance.json`（均在 `/private/tmp/`）。Python 报告读回的
24 份上传模板 DOCX 为 SDK/CLI 各 12 份，48 份内置产物为 SDK/CLI 各 12 DOCX + 12 XLSX，另有 4 份内置与 3 份上传模板预览。
全部使用标明的合成夹具，不是生产用户文档。56 个成功操作是 Python SDK/CLI、本人委托及复制 TypeScript 链路的联合去重结果；
不是全参数覆盖，未观测操作不作成功推断。

API-7 任务续验 `synwevo-api7-jobs-sdk-20260930-r3/acceptance.json`（同在 `/private/tmp/`）另有
18 个成功操作观测，其中覆盖全部 9 个任务操作；这是专项子集，不能替换前批联合 56 的口径。
验收使用已安装 Python SDK 和真实生成器：冻结任务后暂时移开合成模板 blob，产生 11 个真实失败项，
按原摘要恢复模板后以 `failed_only=True` 重试，同一任务第二次运行生成 11 份 DOCX，逐份读取内容并核对 SHA-256。
一个校验失败的跳过项保持原样，任务快照与数据库逐项相符；取消、重放、分页及嵌套/平铺产物清单均实际调用。
服务端历史空值、未知内部字段过滤和响应 schema 由 `test_api_job_models.py` 验证。

## 8. 管理控制台（Synwevo Admin「API 管理」）

| 区域 | 状态 | 证据 |
|---|---|---|
| 总览（调用、失败、耗时、待审批、事件积压、失败订阅） | 已验证 | `/overview`；浏览器实跑 |
| 授权审批、服务凭据（签发、轮换重叠、停用、最近使用） | 已验证 | `test_api_platform*.py`；API-1 |
| 流量与配额 | 已验证 | API-1 |
| Webhook 运行概况与保护性暂停 | 已验证 | API-6 |
| 生成任务视图与保护性停止 | 已验证 | API-6 `test_api_platform_webhook_ops.py` |
| 调用记录（按请求编号/凭据/接口/状态/错误代码） | 已验证 | API-1 |
| 审计（API 平台动作，含拒绝） | 已验证（目录动作本地新增） | API-6；审计行归属目标工作空间；本批包含机器 `invite.*` / `member.*`，保留 client_id、批准人与原审批，普通浏览器成员操作不混入 API 审计；真实查询回归 |
| 受管接口目录与契约 | 已验证（本地，未部署） | `/openapi`、`/operations` 合并 Docs 与目录实际契约，8 个目录操作可检索；仅有效平台管理员会话可访问，组织/空间 owner 不因此获权；不导出账户、管理、OIDC 内部路由或不可达 schema；三语言说明、桌面及手机目录/审批界面已本地实跑 |
| 契约差异、弃用与客户端使用 | 需实现 | API-7 |
| 运行与恢复（入口、依赖、版本） | 需实现 | 与管理员平台「运行」区合并，不在 API 台重复 |
