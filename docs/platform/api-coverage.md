# Synwevo API 平台：能力覆盖表

唯一的 API 能力地图（API 平台专项，约定见 [`../ai/api-platform.md`](../ai/api-platform.md)）。管理员平台的整体覆盖表是
[`admin-coverage.md`](admin-coverage.md)；两者重叠的「API 与集成」一栏以本表为准。

状态词：**已验证**（有测试或实跑证据，列出）· **部分** · **需实现** · **受阻**（外部条件）· **不适用**（说明理由）。
「对外」= 外部系统可用机器凭据调用；「管理」= 平台管理员 `api_platform.manage`；「仅内部」= 只有浏览器会话。
建立于 2026-09-29（三路代码走读 + 实跑），逐条以代码为准；旧记录里的「完成」是线索，不是证据。

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
| **平台管理机器身份**（管理 scope、管理 API 可程序化调用） | 需实现 | `/api/admin/*` 只认会话 Cookie；机器凭据现有两个资源（Docs、工作空间目录，API-5 已把审批与验证泛化） | 批次 API-3，依赖管理员平台 B1-2 委派权限 |

## 2. 契约、错误与追踪

| 能力 | 状态 | 实现与证据 | 缺口 |
|---|---|---|---|
| OpenAPI 3.1 由实际路由导出，漂移测试 | 已验证 | `python -m synwevo_docs.cli openapi`；`test_api_docs.py` | 70 个响应无类型；管理 API 不在契约里 |
| 错误信封 + 可选 RFC 9457（`Accept: application/problem+json`） | 已验证 | `http.py`；`test_gate_api_errors.py` | 类型 URN 不可解引用 |
| `X-Request-ID` 全链路回显、调用记录 | 部分 | `http.py` 网关；**API-1**：调用记录带错误代码与客户端，可按请求编号/凭据/接口/状态/错误代码查询；网关拒绝的请求记在接口模板下 | **API-2**：提交任务的请求编号记在任务 `request_id` 与 `job.created`（`test_request_trace.py`）；无 traceparent |
| 分页：offset 与游标 | 已验证 | `v1.py:181-240` | 游标未签名（安全依赖每个查询的租户过滤） |

## 3. 流量、配额与用量

| 能力 | 状态 | 实现与证据 | 缺口 |
|---|---|---|---|
| 每凭据请求速率（跨进程共享） | 已验证 | **API-1** `api_quotas.RateGate`（GCRA，整数纳秒，库内状态）；`test_every_process_shares_one_budget`、`test_the_arithmetic_admits_exactly_the_budget_at_any_size` | — |
| 每工作空间合计请求速率 | 已验证 | **API-1**；`test_a_workspace_budget_holds_across_its_credentials_and_nowhere_else`；隔离实例实跑两把密钥第 4 次 429 | — |
| 轮换不换额度（按密钥谱系计） | 已验证 | `test_rotation_does_not_buy_a_fresh_credential_budget` | — |
| 同时进行的生成任务（网页与 API 同一上限） | 已验证 | `jobs._admit` 读工作空间策略；`test_a_workspace_policy_replaces_the_deployment_limit` | — |
| 管理员编辑（平台/工作空间/凭据层，继承、版本、预演、历史、审计） | 已验证 | **API-1** `/api/admin/api-platform/quotas*`；控制台「流量与配额」；隔离实例浏览器实跑（预演 → 重新验证 → 保存 → 实际 429） | 手机上调用记录表格需横向滚动（UI 批次改卡片） |
| 存储、页数、生成额度、用量账本 | 需实现 | 只有实时队列 `/usage` | 没有计费业务：只做用量与额度，不编造价格 |

## 4. Docs 业务 API（`/api/v1`）

| 能力 | 状态 | 实现与证据 | 缺口 |
|---|---|---|---|
| 项目、来源、续传上传、模板（上传/内置编辑器）、规则、映射、定义、预检、预览、任务、产物、下载链接、ZIP、报告 | 已验证 | 90 个操作；`test_api_platform_pipeline.py`；SDK 实跑 12 份 DOCX（`api-platform-evidence/`） | — |
| 幂等（数据库唯一约束、同键冲突、重放复核） | 已验证 | `idempotency.py`、`jobs.py` | Webhook 创建/轮换/ping/重投拒收幂等键；恢复版本、组织发布无幂等 |
| OCR 触发 | 已验证 | **API-2** `POST /api/v1/files/{fid}/ocr`（同一服务、未接纳 409、识别槽位 429 `ocr_busy`）；`test_machine_ocr_*`；SDK 实跑 | 同步执行（≤50 页）；超长识别未做成任务 |
| 上传模板的字段修改/删除 | 已验证 | **API-2** `PATCH`/`DELETE /templates/{tid}/fields/{field_id}`（`base_head` 条件更新）；`test_api_template_fields.py`；SDK 实跑 | 整份定义替换（按定义对账）未做 |
| 项目改名/归档/输出默认值 | 已验证 | **API-2** `PATCH /api/v1/projects/{pid}`（`base_settings_rev` 必填、同事务）；`test_api_project_settings.py`（4，含 6 路并发） | 规则试算（不保存）仍为浏览器专有 |
| 跨空间复制（审批）、评论/提及、活动 | 需实现 | 浏览器会话专有 | 批次 API-5 |
| 组织模板 | 部分 | 只接受委托令牌（成员身份属于人） | 设计如此；服务账户场景待定 |

## 5. 事件与 Webhook

| 能力 | 状态 | 实现与证据 | 缺口 |
|---|---|---|---|
| 事务 outbox（任务事件与状态同事务） | 已验证 | `jobs.py`、`webhooks.py` 调度游标 | 事件与投递表无保留期 |
| Standard Webhooks 签名、轮换重叠、SSRF（每次解析、连接已验证 IP、拒绝跳转） | 已验证 | `webhooks.py`；`test_webhook_*` | 只用第一个解析地址 |
| 重试、死信、人工重投、暂停/恢复 | 已验证 | `webhooks.py` | 暂停期间事件丢弃；无批量重放 |
| 事件目录 | 部分 | `job.*`、`webhook.ping`；**API-2** `file.ready/needs_ocr/rejected/failed`（触发器与状态同事务写入；`test_file_events.py` 8 项） | 模板、复制、成员事件（批次 API-5）。任务完成事件不带产物清单是有意的：负载只给计数，产物用凭据读取，撤权即时生效 |
| 控制台跨工作空间 Webhook 视图 | 已验证 | **API-6** 投递运行概况（元数据、接收方只显示来源）与保护性暂停；总览事件积压；`test_api_platform_webhook_ops.py`（3）；隔离实例浏览器实跑 | 平台侧重投不做（属于工作空间）；跨空间投递明细不显示负载 |

## 6. 其他模块的机器 API

| 模块 | 状态 | 说明 |
|---|---|---|
| 工作空间成员、邀请（只读） | 已验证 | **API-5** `/api/workspace/v1`（独立资源与受众、所有者批准的服务账户、同一限流与调用记录）；`test_workspace_directory_api.py`（4） | 邀请/改角色/移除的机器写入需要应用操作者（API-5b）；应用资格未开放 |
| 成员导入 | 需实现 | 已是异步任务 + 预检 + 确认；需组织级机器主体 |
| 通知发出、对象卡片 | 需实现 | 现只能由 Docs 内部 outbox 产生；外部集成无入口 |
| Message（会话、消息、机器人） | 需实现 | 全部会话专有；机器人身份需清楚标识 |
| 管理只读（审计、调用、配额） | 需实现 | 批次 API-3 |

## 7. SDK

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| Python SDK 0.1.0（重试、幂等键、流式下载、续传、Webhook 验签） | 已验证 | `apps/docs/sdk/python/tests`（21）；真实服务验收 | 覆盖清单过期；验收不在 CI |
| TypeScript SDK 0.1.0（单文件 `.mjs`） | 部分 | `test/client.test.mjs`（7）；**API-7** 类型声明 `synwevo-docs.d.ts`（43 个导出与全部方法，按实际路由代码核对）、私有 `package.json`（`npm pack` 可本地打包，未发布）、`test/types-check.ts` 严格模式通过（含 6 处 `@ts-expect-error`）；修正 `generate` 进度里字段版本始终为空 | 缺源文件检视、恢复、凭据、签名链接；同步抛出的 ValidationError 类型无法表达（已在声明头部说明） |
| 多策略 `RateLimit` 头解析 | 已验证 | **API-1** 两个 SDK 取 `r=0` 各项最长 `t` | — |

## 8. 管理控制台（Synwevo Admin「API 管理」）

| 区域 | 状态 | 证据 |
|---|---|---|
| 总览（调用、失败、耗时、待审批、事件积压、失败订阅） | 已验证 | `/overview`；浏览器实跑 |
| 授权审批、服务凭据（签发、轮换重叠、停用、最近使用） | 已验证 | `test_api_platform*.py`；API-1 |
| 流量与配额 | 已验证 | API-1 |
| Webhook 运行概况与保护性暂停 | 已验证 | API-6 |
| 生成任务视图与保护性停止 | 已验证 | API-6 `test_api_platform_webhook_ops.py` |
| 调用记录（按请求编号/凭据/接口/状态/错误代码） | 已验证 | API-1 |
| 审计（API 平台动作，含拒绝） | 已验证 | API-6；审计行归属目标工作空间（修复会话工作空间回退） |
| 接口契约 | 已验证 | 现有；三语言说明测试 |
| 契约差异、弃用与客户端使用 | 需实现 | API-7 |
| 运行与恢复（入口、依赖、版本） | 需实现 | 与管理员平台「运行」区合并，不在 API 台重复 |
