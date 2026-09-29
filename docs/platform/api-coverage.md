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
| **第三方客户端同意页、已连接应用管理** | 需实现 | `/oidc/authorize` 在会话与资格检查后直接发码 | 批次 API-4 |
| 令牌自省（RFC 7662）、`client_secret_basic` | 需实现 | README 声称有自省，实际没有 | 批次 API-4（同时更正 README） |
| **平台管理机器身份**（管理 scope、管理 API 可程序化调用） | 需实现 | `/api/admin/*` 只认会话 Cookie；机器凭据只限 Docs 资源 | 批次 API-3，依赖管理员平台 B1-2 委派权限 |

## 2. 契约、错误与追踪

| 能力 | 状态 | 实现与证据 | 缺口 |
|---|---|---|---|
| OpenAPI 3.1 由实际路由导出，漂移测试 | 已验证 | `python -m synwevo_docs.cli openapi`；`test_api_docs.py` | 70 个响应无类型；管理 API 不在契约里 |
| 错误信封 + 可选 RFC 9457（`Accept: application/problem+json`） | 已验证 | `http.py`；`test_gate_api_errors.py` | 类型 URN 不可解引用 |
| `X-Request-ID` 全链路回显、调用记录 | 部分 | `http.py` 网关；**API-1**：调用记录带错误代码与客户端，可按请求编号/凭据/接口/状态/错误代码查询；网关拒绝的请求记在接口模板下 | 任务、事件、Webhook 投递不带 request_id；无 traceparent |
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
| 控制台跨工作空间 Webhook 视图 | 需实现 | 现在要在管理员浏览器里粘贴机器凭据 | 批次 API-6 |

## 6. 其他模块的机器 API

| 模块 | 状态 | 说明 |
|---|---|---|
| 工作空间成员、邀请、应用资格 | 需实现 | 逻辑在 core（含最后所有者保护）；需要非 Docs 的资源与 scope（批次 API-5） |
| 成员导入 | 需实现 | 已是异步任务 + 预检 + 确认；需组织级机器主体 |
| 通知发出、对象卡片 | 需实现 | 现只能由 Docs 内部 outbox 产生；外部集成无入口 |
| Message（会话、消息、机器人） | 需实现 | 全部会话专有；机器人身份需清楚标识 |
| 管理只读（审计、调用、配额） | 需实现 | 批次 API-3 |

## 7. SDK

| 能力 | 状态 | 证据 | 缺口 |
|---|---|---|---|
| Python SDK 0.1.0（重试、幂等键、流式下载、续传、Webhook 验签） | 已验证 | `apps/docs/sdk/python/tests`（21）；真实服务验收 | 覆盖清单过期；验收不在 CI |
| TypeScript SDK 0.1.0（单文件 `.mjs`） | 部分 | `test/client.test.mjs`（7） | 无类型声明、无 npm 包；缺源文件检视、恢复、凭据、签名链接 |
| 多策略 `RateLimit` 头解析 | 已验证 | **API-1** 两个 SDK 取 `r=0` 各项最长 `t` | — |
