# Model Context Protocol（MCP）：设计记录

状态：**本地实现，测试通过的范围见 §9，尚未部署**。平台策略的内置默认是两个方向都关（§5），所以合并本分支不会
让任何部署多出一个可调用的端点或一条出站连接；管理员发布一版策略后才生效。真实模型 + 真实外部 MCP 服务的联调
**尚未做**，不能据本文宣称。使用说明在 [`../integration/mcp.md`](../integration/mcp.md)；本文记录做了什么、没做什么和为什么。

两个方向，两个包，互不依赖：

| 方向 | 含义 | 代码 |
|---|---|---|
| 入站（§3） | Synwevo 自己是一个 MCP 服务端：外部 MCP 客户端经账户服务授权后，调用业务应用的操作 | `apps/suite/synwevo_suite/mcp_server/` |
| 出站（§4） | 个人 Agent 是一个 MCP 客户端：连接外部 MCP 服务，发现其工具、资源、提示词，在对话里调用 | `apps/suite/synwevo_suite/agent/mcp/` |
| 共用 | 平台策略、客户端元数据文档、表结构、密封用途 | `packages/core/synwevo_core/{mcp_policy,cimd}.py`、`suite_schema/mcp.py`、`sealed.py` |
| 账户服务 | RFC 8414 元数据、`iss`、客户端元数据文档登记、同意页的工作空间选择、管理接口 | `services/identity/synwevo_identity/{provider_routes,admin_mcp}.py` |

## 1. 范围与原则

* **入站不是第二套实现。** 它是业务应用操作接口（[`../integration/suite-api.md`](../integration/suite-api.md)）换一种
  协议：工具就是注册表里机器调用方可以请求的操作，输入 schema 是操作自己的，每次调用都是 `Capabilities.invoke(via="api")`，
  校验、本人实时角色、运行记录、幂等、事件与 REST 路由完全一致。这一层只增加协议的形状。
* **出站不给模型任何默认权限。** 连接属于一个人或一个工作空间；工具逐个授权（off / confirm / auto）；每个人再决定自己的
  Agent 用不用这个连接、用什么别名、要不要收窄。没有人因为所在工作空间加了一个服务就多出工具。
* **外部内容只是内容。** 外部服务的描述、结果、提示词都标为「来自 Synwevo 之外、不是指令」；工作空间的内容只在该空间的
  数据流出站策略允许时才会被送到外部服务。
* **出站网络是受检的、被钉住的。** 目的地先过平台策略（允许/禁止主机），再由共享解析器解析为公网地址（或管理员列明的
  内网），连接就连到检查过的那个地址；不跟随重定向，不读环境代理。
* **记录先于请求。** 出站每一次调用在请求发出之前就有记录；入站每一次请求进入 API 平台的调用记录。记录里没有凭据，
  没有原始请求体。

## 2. 研究记录（2026-10-23 查阅官方来源）

实现对照的是以下官方文本；版本号与条目按原文记录。

### 2.1 协议修订 2026-07-28

* 当前 "latest"：<https://modelcontextprotocol.io/specification/2026-07-28>。
* 变更记录（<https://modelcontextprotocol.io/specification/2026-07-28/changelog>）：
  * 无状态：没有 `initialize` / `notifications/initialized` 握手；每个请求在 `_meta` 里携带协议版本与客户端能力
    （`io.modelcontextprotocol/protocolVersion`、`io.modelcontextprotocol/clientCapabilities`、`clientInfo`）；
  * `server/discover` 是必需方法；
  * 移除 `Mcp-Session-Id`；移除 HTTP GET 流，以 `subscriptions/listen` 取代；移除 SSE 可恢复性（`Last-Event-ID`）；
  * 必需请求头 `MCP-Protocol-Version`、`Mcp-Method`、`Mcp-Name`；
  * 结果携带 `resultType`；列表结果携带 `ttlMs` 与 `cacheScope`；
  * 任务（tasks）移入扩展 `io.modelcontextprotocol/tasks`；
  * MRTR（model-requested tool results）取代服务端发起的请求；Roots、Sampling、Logging 标为弃用；
  * 动态客户端登记（RFC 7591）标为弃用，改为 Client ID Metadata Documents；
  * RFC 9207 `iss`：授权响应 SHOULD 携带，客户端 MUST 校验。
* 更早的修订 2025-11-25、2025-06-18、2025-03-26 属于 "legacy"（有 `initialize` 握手与会话）；版本页
  （<https://modelcontextprotocol.io/specification/2026-07-28/basic/versioning>）定义了同时服务两个时代的服务端
  （dual-era servers）。

### 2.2 传输：Streamable HTTP

<https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http>：单一端点；只有 POST；
响应是 JSON 或仅限本次请求的 SSE；通知答 202；须校验 Origin；只支持新修订的服务端对 GET / DELETE 答 405；
请求头不一致答 400 `HeaderMismatch`（-32020）；不支持的协议版本 `UnsupportedProtocolVersion`（-32022）；未知方法答
404 并带 -32601。

### 2.3 授权

<https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization> 与
<https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization/client-registration>：

* MCP 服务端是 OAuth 2.1 资源服务器；服务端 **必须** 提供 RFC 9728 受保护资源元数据；
* 客户端 **必须** 使用 RFC 8707 资源指示（`resource`），值为服务端的规范 URL；
* 授权服务器元数据经 RFC 8414 或 OpenID Connect Discovery；PKCE S256；
* 客户端元数据文档（Client ID Metadata Documents）**SHOULD** 支持：`client_id` 是带路径的 https URL；文档必须包含
  `client_id`、`client_name`、`redirect_uris`；授权服务器拉取文档、校验 `client_id` 相等与回调地址、按 HTTP 缓存头缓存；
  以 `client_id_metadata_document_supported: true` 宣告；预登记仍是一个选项；
* 401 带 `WWW-Authenticate: Bearer resource_metadata="…"`，可带 `scope`；403 `insufficient_scope` 带 `scope` 以便升级权限。

### 2.4 工具

<https://modelcontextprotocol.io/specification/2026-07-28/server/tools>：`tools/list` 顺序确定；工具名 1–128 字符，
字符集 `[A-Za-z0-9_.-]`；注解（`readOnlyHint`、`destructiveHint`、`idempotentHint`、`openWorldHint`）是**不可信的提示**；
执行错误是 `isError: true` 的结果；未知工具是 JSON-RPC -32602 错误；`x-mcp-header` 头镜像。

### 2.5 SDK

官方 Python SDK，PyPI 包 `mcp` **2.3.0**（2026-10-02 发布，<https://pypi.org/project/mcp/>），`Requires-Python >=3.10`，
文档 <https://py.sdk.modelcontextprotocol.io/>。v2.0.0（2026-07-28 发布，
<https://github.com/modelcontextprotocol/python-sdk/releases/tag/v2.0.0>）实现修订 2026-07-28，并在同一个服务端上服务
所有更早的修订；v1.x 进入维护期（只修安全问题）。

锁定在 `infra/server/requirements.lock`（抽象依赖在 `apps/suite/requirements.txt` / `pyproject.toml`）：`mcp 2.3.0`、
`mcp-types 2.3.0`、`jsonschema 4.26.0`、`jsonschema-specifications 2025.9.1`、`referencing 0.37.0`、`rpds-py 2026.9.1`、
`attrs 26.1.0`、`sse-starlette 3.5.0`、`opentelemetry-api 1.45.1`、`PyJWT 2.15.1`；`httpx2 2.13.1` 本来就在。许可证见
[`../DEPENDENCIES.md`](../DEPENDENCIES.md) §1.1。

**选择理由。** 官方 SDK 是唯一同时服务新旧两代修订的实现，而 Agent 要连接的外部服务两代都有；自己实现 JSON-RPC +
SSE + 两代握手不是本项目的价值所在。代价：多了十个依赖；`anyio`/SSE 的服务端任务组需要在进程里启动一次（§3.5）。

## 3. Synwevo 作为 MCP 服务端（入站）

### 3.1 位置与元数据

| 项 | 值 |
|---|---|
| 端点 | 工作空间源站 `{SYNWEVO_APP_URL}/api/suite/v1/mcp`，只登记 `POST`（`synwevo_core.http.MCP_PATH`） |
| 规范 URI / 令牌受众 | `synwevo_core.oidc.mcp_resource()`：`SYNWEVO_MCP_RESOURCE`，否则 `{SYNWEVO_APP_URL}/api/suite/v1/mcp`；两者都没有时是 `urn:synwevo:platform:mcp:unconfigured`——没有客户端会用它申请令牌，受众检查因此不可能误通过 |
| 受保护资源元数据（RFC 9728） | `GET {SYNWEVO_APP_URL}/.well-known/oauth-protected-resource/api/suite/v1/mcp`：`resource`、`authorization_servers=[issuer]`、`scopes_supported`（10 个业务 scope：read 与 write；`*.decide` 不默认宣告，客户端需明确申请）、`bearer_methods_supported=["header"]`、`resource_name="Synwevo MCP"`；用 SDK 的 `ProtectedResourceMetadata` 模型生成；`Cache-Control: public, max-age=300`、`Access-Control-Allow-Origin: *` |
| 角色 | 元数据路径与 Agent 的客户端元数据文档只由 `portal` 角色回答；其它角色答「MCP 服务在工作空间源站上」。生产网关把 `workplace.synwevo.com` 整个转给 portal 容器，不需要新的 location |
| 受众与 REST 的关系 | 同一批 scope（`MCP_SCOPES = SUITE_API_SCOPES`），不同受众：REST 的令牌（`urn:synwevo:platform:suite`）打不开 MCP（`invalid_audience`），反之亦然。客户端想两者都用，就登记两个受众并按资源分别申请令牌 |

### 3.2 谁能调用

网关在解析任何 JSON-RPC 之前决定（`MachineAuth.verify_mcp`，`packages/core/synwevo_core/machine.py`）；顺序即下表：

| 检查 | 拒绝 |
|---|---|
| `Authorization: Bearer` 存在 | 401 `unauthorized`，`WWW-Authenticate: Bearer error="invalid_token", resource_metadata="…"` |
| 策略 `inbound_enabled` | 403 `mcp_inbound_disabled`（challenge `insufficient_scope`） |
| 不是 Docs 的 `dw_` 密钥 | 401 `wrong_credential` |
| 令牌存在、未过期、未撤销 | 401 `unauthorized` |
| 令牌受众 == `mcp_resource()` | 401 `invalid_audience`（`details.expected` 给出该带的 `resource`） |
| 其余与 REST 共用 `_suite_delegation`：客户端未停用（`client_unavailable`）、属于 `platform` 应用（`wrong_audience`）、是本人授权而非服务账户（`wrong_credential` / `service_account_not_supported`）、持有且客户端仍登记业务 scope（`wrong_credential` / `forbidden`）、账户可用、绑定了工作空间（`no_workspace`）、空间与组织可用、本人仍是成员 | 对应代码；401/403 都带 RFC 6750 challenge |

得到的主体 `transport="mcp"`，之后进同一个速率门（与 REST 共用按凭据、按工作空间的限额）和同一张调用记录表
（`api_platform_calls`，route `/api/suite/v1/mcp`）。ASGI 包装（`mcp_server/asgi.py`）再核对一次主体就是它要的形状，缺了
就 401 而不是猜。

### 3.3 工具：什么被暴露，什么没有

`McpTools.visible(principal)`：`machine_operations(registry)` 里 **应用在该令牌的工作空间对本人可用**（`Apps.check_access`，
且 `AppManifest.machine` 为真）**并且** `{操作的 scope} ∪ 操作的 reaches ⊆ 令牌持有的 scope` 的操作，按 id 排序；
另加两个平台工具 `synwevo.whoami`、`synwevo.run.get`。列表 `ttl_ms=60_000`、`cache_scope="private"`。

一个操作如何变成一个工具定义（`McpTools.tool`）：

| 工具字段 | 来源 |
|---|---|
| `name` | 操作 id（`forms.form.list`）；形式满足 §2.4 的字符集 |
| `title` / `description` | 英文标题；摘要 + 一句事实（只读 / 改工作空间数据 / 触达平台外的人）+ 异步说明 + 幂等说明 + `Also needs …` |
| `inputSchema` | 操作自己的输入 schema，加 `idempotency_key`（有副作用的操作可带；契约里 required 的操作必带，并列入 `required`）与可选 `correlation_id` |
| `outputSchema` | 信封 `{run_id, operation, status, output, replayed, workspace_id, truncated}`，`output` 是操作声明的输出 schema |
| `annotations` | `readOnlyHint` = 副作用为 none；`destructiveHint` = 有副作用且 id 以 `.delete/.archive/.cancel/.withdraw/.revoke/.close` 结尾；`idempotentHint` = 只读或接受幂等键；`openWorldHint` = 副作用为 external |
| `_meta` | `com.synwevo/scope`、`app`、`side_effect`、`mode`、`version` |

**不暴露的，是边界不是遗漏：**

* **Synwevo Docs（`docweave.*`）与 Synwevo Message（`messages.*`）**：它们的适配器不是机器应用（`AppManifest.machine` 为假），
  `machine_operations` 里本来就没有它们，REST 操作接口同样没有。Docs 的 MCP 端点如果要做，属于 Docs 源站
  （`docwave.synwevo.com`），由 Docs 自己的凭据与授权规则决定，不在工作空间源站上借用业务应用的受众。
* **个人 Agent 的一切**：`agent.*` 操作 `need="personal"`，能力层对 `via="api"` 一律 `person_required`。
* **提示词（prompts）**：没有声明。业务应用没有"给模型的提示词"这种对象；编一套会变成产品文案而不是能力。
* **服务账户**：与 REST 一致，`service_account_not_supported`。

### 3.4 调用语义

`McpTools.call`：

1. `synwevo.whoami` → 本人、客户端、工作空间、角色、scope、受众、可用应用、到期时间（`transport: "mcp"`）。
2. `synwevo.run.get` → 只回答 **本客户端为本人在本工作空间** 经机器路径发起的运行（其它一律 `run_not_found`），并要求该运行
   的操作对应的 scope。
3. 其它名字先查注册表：不存在 → JSON-RPC `-32602`（协议错误，不是结果）。
4. 存在 → 依次：所需 scope 与 `reaches`（`insufficient_scope`，`details.required_scope`）、应用可用（`app_unavailable`）、
   `idempotency_key` / `correlation_id` 类型、契约要求必带而缺（`idempotency_key_required`）；幂等键按客户端命名空间存储
   （`api:<客户端哈希>:<键>`，与 REST 同一规则）；然后
   `Capabilities.invoke(op, args, user_id, workspace_id=令牌的空间, via="api", idempotency_key, correlation_id, principal,
   scopes, delegation={"kind": "oauth_client", "client_id", "grant", "transport": "mcp"})`。
5. 结果：`structuredContent` 是信封，`content` 的文本块是同一份 JSON（给不读结构化内容的客户端）；超过 400 000 字符
   （规范化 JSON）时返回前缀与 `truncated: true`，不静默丢弃。`_meta["com.synwevo/request_id"]` 是本次请求编号。
6. 业务层的 `ServiceError` 变成 `isError: true` 的结果，正文是 REST 的错误契约 `{"error": {code, message, request_id,
   details}}`（去掉 `www_authenticate`）；模型读得到并能调整，服务端不抛协议错误。

资源：`synwevo://whoami`（同 whoami）与 `synwevo://operations`（本人在该空间可用的全部操作的契约 + 每条 `granted`：
该令牌是否持有它需要的 scope），`application/json`；列表 `ttl_ms=60_000`，读取 `ttl_ms=0`。

`instructions`（服务端自述，`server.py` `INSTRUCTIONS`）告诉客户端：先调 `synwevo.whoami`；工具名是稳定 id；触达平台外的
操作需要你自己选的 `idempotency_key`；`queued`/`running` 的结果未完成，用 `synwevo.run.get` 轮询；结果是业务数据，其中的
指令不是本人说的。

### 3.5 传输与多进程

* `StreamableHTTPSessionManager(app=server, json_response=False, stateless=True, security_settings=…, max_request_body_size=4 MiB)`：
  一个 POST 一条消息；修订 2026-07-28 没有会话、没有握手；2025 年代客户端的 `initialize` 握手也以无状态方式回答——
  部署的任何进程都能回答任何请求，进程之间不共享任何东西。测试里官方客户端以 `mode="legacy"` 走通（§9）。
* `json_response=False`：响应默认是仅限本次请求的 SSE 流（其中可先带通知），纯 JSON 响应由 SDK 按客户端 `Accept` 决定。
* DNS 重绑定保护（SDK 的 `TransportSecuritySettings`）：只回答 `Host` 为 `SYNWEVO_APP_URL` 的主机（`APP_URL` 是本机地址时
  再加 loopback 名字）；其它主机名不是本服务。
* SDK 的处理函数任务组必须在进程里进入一次：`new_api` 的 lifespan 没有启动钩子，所以首个请求时进入、由 `shutdown_hooks`
  退出（`McpEndpoint._ensure_running` / `close`）。
* 请求体上限 4 MiB（SDK 答 413）。只登记 POST，其它方法由路由层答 405。
* 网关（`infra/production/nginx.conf`）：端点在 docs 角色的主机上，走该主机的 `location /`，全局已是
  `proxy_buffering off` / `proxy_request_buffering off`，所以 SSE 响应逐条到达客户端；读超时 300 s，与机器 API 的 REST
  请求相同。2026-07-28 没有长连接的 GET 流，不需要像协同编辑那样单开 `location`。前面的 Cloudflare 对一个没有字节的
  源站响应有自己的等待上限（非企业套餐约 100 s），它同样作用于 REST；耗时的业务操作本来就是异步的——工具立即返回
  `run_id`，客户端用 `synwevo.run.get` 轮询，单个请求不长时间占着连接。

### 3.6 账户服务的配套

| 项 | 实现 |
|---|---|
| RFC 8414 | `GET {SYNWEVO_ISSUER}/.well-known/oauth-authorization-server` 与 OIDC Discovery 同一份文档（`oidc.discovery`）；`code_challenge_methods_supported=["S256"]`；`authorization_response_iss_parameter_supported: true`；`client_id_metadata_document_supported` 取自策略 `client_registration == "cimd_and_preregistered"` |
| RFC 9207 `iss` | `/oidc/authorize` 的每个重定向（成功、错误、同意页之后）都带 `iss=<issuer>` |
| MCP 客户端的 scope 收窄 | 按 MCP 授权规范的 scope 选择规则，标准客户端会申请元数据宣告的全部 scope；以前 `authorize` 拒绝任何未登记的 scope，于是只登记了部分 scope 的客户端永远授权不了（官方 SDK 的 `OAuthClientProvider` 实测撞上）。现在对登记了 MCP 受众的客户端，申请收窄到登记范围（RFC 6749 §3.3），令牌响应写明实际 scope；收窄后不剩登记范围内的业务 scope 才拒绝。其他客户端照旧拒绝未登记 scope |
| 客户端元数据文档（`synwevo_core.cimd`） | `client_id` 形如 https URL（有主机、有路径、无 query/fragment/凭据）且未登记时，`authorize` 让 `ClientMetadataDocuments.resolve` 处理：策略允许该机制且该主机（`cimd_allowed`）；已存在且被停用 → 拒绝；`registration='cimd'` 的登记 24 小时内直接复用，否则重新拉取；运维手工登记的同名 id 以手工登记为准。拉取走 `webhook_delivery.resolve_checked` + `pinned_opener`：https、公网地址、不跟随重定向、64 KiB、10 秒、`Content-Type` 为 `application/json`（或缺省）。校验：`client_id` 与地址相等；`redirect_uris` 1–10 条且每条过 `_check_redirect`（非本机必须 https）；`token_endpoint_auth_method` 只能是 `none`（本平台不给没见过的客户端发密钥）；`grant_types` 含 `authorization_code`；`client_name` 去控制字符、≤120 字。通过后登记为 `platform` 应用的 **公共** 用户客户端，scope `openid profile email offline_access` + 12 个业务 scope，受众只有 `mcp_resource()`；文档原文（≤64 KiB）与拉取时间存入 `oauth_clients.metadata_json / metadata_fetched_at`；审计 `oidc.client_metadata_document` |
| 同意页 | `GET /api/auth/oauth-consent/{rid}` 多返回 `client.registration`（`operator` / `cimd`）、`client.client_uri`、`workspace_choices`（`null`，或 `[{id, name, role, consented}]`）。何时给选择：客户端属于 `platform` 应用、申请了需要同意的 scope、且 `authorize` 没带 `workspace_id` 时——本人此前对该客户端只在一个在用空间同意过且覆盖所申请 scope → 直接用它；只有一个可用空间 → 直接用；否则列出让本人选。`POST …/approve` 接受 JSON `{workspace_id}`；给了选择而没选是 400 `workspace_required`。同意页（`services/identity/web/src/pages/OAuthConsent.tsx`）渲染单选列表，标出已授权过的空间，未选择时「批准」不可用；`cimd` 客户端标明 `client_uri` 主机并说明未核实运营方 |
| 管理接口 | §5 |

## 4. Agent 作为 MCP 客户端（出站）

### 4.1 连接与归属（`connections.py`）

| 项 | 规则 |
|---|---|
| 归属 | `owner_kind='user'`（本人管理、只有本人可用）或 `'workspace'`（所有者 / 管理员管理，成员可用；空间须 active） |
| 看不到 = 不存在 | 非成员对工作空间连接、他人对个人连接：404 `mcp_connection_not_found`；成员要管理：403 `mcp_manage_forbidden` |
| 地址 | http(s) 绝对地址，无片段、无账号口令；明文 http 一律需策略 `allow_http`（loopback 也一样，保存与使用同一规则）；主机过策略（`mcp_host_blocked` / `mcp_host_not_allowed`）；≤2048 字符 |
| 数量 | 每人 `max_connections_per_user`（默认 10）、每空间 `max_connections_per_workspace`（默认 20）：409 `mcp_connection_limit` |
| 认证 | `none`、`bearer`（令牌密封，`credential_hint` 只留末四位）、`oauth`（§4.3；可填预登记的 `client_id` 与可选 `client_secret`，密封） |
| 改地址 | 新地址视为新服务：已发现的工具、资源、提示词标为移除，健康与熔断清零，授权随 schema 哈希自然失效 |
| 删除 | 软删除，密封列清空，绑定与进行中的授权流程删除；记录保留 |
| 停用 | `status='disabled'`：任何使用 409 `mcp_connection_disabled`；平台管理员也可停用（§5，同时忘掉凭据） |

每次写入都写审计（`suite.agent.mcp.*`，见 `common/audit_actions.py` 与账户侧 `app_actions.py` 的标签），meta 只有
主机名、认证方式、改了哪些字段，没有地址路径、没有凭据。

### 4.2 发现、授权、绑定

**发现**（`test` / `refresh`）是真实握手：SDK 客户端连上去读服务端能力，再按能力分页读 `tools/list`、`resources/list`、
`prompts/list`（每类最多 500 条）。每个工具存下名字、标题、描述（≤4000 字）、输入/输出 schema、注解和一个
**schema 哈希**（`tool_hash`：名字 + 输入 schema + 输出 schema + `readOnlyHint` + `destructiveHint`——描述变了不算，形状或只读
提示变了才算）。服务端不再列出的工具标 `removed_at`，再出现时恢复。协议版本、`serverInfo`、能力与 `instructions` 存在连接上。

**授权**（`mcp_tool_grants`，连接级，管理者设置）：

| 模式 | 含义 | 限制 |
|---|---|---|
| `off` | Agent 不得使用 | — |
| `confirm` | 可用，但每次调用先给本人看确认卡 | — |
| `auto` | 不经确认执行 | 只允许服务端标 `readOnlyHint: true` 的工具（409 `mcp_auto_requires_read_only`）；注解是不可信提示（§2.4），所以 auto 仍受 §4.5 的数据流检查与结果标注 |

授权钉在设置时的 schema 哈希上：服务端改了工具的形状，授权变 **stale**，有效模式按 `off` 算，直到有人再看一眼并重新设置。
个人连接首次发现时每个工具自动得到 `confirm`（本人看自己的服务），之后新出现的工具不自动获得任何授权；工作空间连接
在管理员决定之前没有任何授权。

**绑定**（`mcp_agent_bindings`，个人级）：本人决定自己的 Agent 用不用这个连接，别名（slug，≤24 字符，本人范围内唯一，
409 `mcp_alias_taken`），是否把资源开放给 Agent（默认不开），以及按工具的个人模式——只能是 `off` 或 `confirm`，即只能收窄
连接级授权，不能放宽。

`effective_tools(user)` 是 Agent 看到的最终集合：策略 `outbound_enabled` 为真 ∧ 绑定启用 ∧ 连接 active 且本人仍可用 ∧
授权不是 off 且不 stale ∧ 个人模式再收窄。顺序确定（别名、工具）。

### 4.3 认证：Bearer 与 OAuth（`oauth.py`）

Bearer：本人粘贴令牌，密封保存（用途 `mcp-credential`），每次交换时解封进 `Authorization` 头，不进日志、预览或结果。

OAuth 按 §2.3 的流程，**由服务端驱动**——浏览器不接触外部服务的凭据：

1. `agent.mcp.oauth.start`（管理者；连接须为 `oauth` 类型，否则 409 `mcp_auth_kind_mismatch`；部署须有 `SYNWEVO_APP_URL`，否则
   503 `mcp_oauth_unavailable`）。
2. **发现**：向服务地址 POST 一条 `server/discover`（2026-07-28 头与 `_meta`）。期望 401：从 `WWW-Authenticate` 取
   `resource_metadata` 与 `scope`；按 SDK 的候选地址读 RFC 9728 文档，取第一个授权服务器与 `resource`；没有资源元数据的
   2025-03-26 服务以自身源站为授权服务器、以自身地址为 `resource`；再按候选地址读 RFC 8414 / OIDC 元数据并校验 `issuer`。
   服务端不要求认证（答 2xx/400/404/405/406）→ 409 `mcp_auth_not_required`；找不到元数据 → 502 `mcp_oauth_discovery_failed`；
   不支持 S256 → 502 `mcp_oauth_unsupported`。
3. **客户端身份**，按规范的优先级：本人预登记的 `client_id`（可带密钥，走 Basic）→ 此前对同一 issuer 动态登记过的 id →
   **本平台的客户端元数据文档**（授权服务器宣告支持且 `SYNWEVO_APP_URL` 是 https）→ 授权服务器提供 `registration_endpoint`
   时的动态登记（公共客户端；失败 502 `mcp_oauth_registration_failed`）→ 都没有则 409 `mcp_oauth_registration_unavailable`，
   提示先到对方登记并填写 `client_id`。
4. PKCE S256、`state`（32 字节）、`resource`、发现得到的 `scope`——授权服务器的元数据列出 `offline_access` 时加上它，
   否则拿不到刷新令牌，访问令牌一过期连接就要本人重新授权；流程行（`mcp_oauth_flows`，code_verifier 密封）有效 600 秒；
   返回 `authorization_url`，页面把浏览器送过去。审计 `suite.agent.mcp.oauth.start`。
5. **回调** `GET {SYNWEVO_APP_URL}/api/suite/agent/mcp/oauth/callback`，在本人已登录的会话里：`state` 对应的流程须属于本人
   （404 `mcp_oauth_flow_not_found`）、未处理（409 `mcp_oauth_flow_decided`）、未过期（410 `mcp_oauth_flow_expired`）；
   `iss` 存在则必须等于发现时的 issuer，授权服务器宣告会发而没发同样拒绝（400 `mcp_oauth_issuer_mismatch`）；授权服务器
   返回 `error` → 400 `mcp_oauth_denied`。然后在钉住的连接上换码（不跟随重定向；失败 `mcp_oauth_token_failed`），令牌组
   密封存到连接上，流程关闭，审计 `suite.agent.mcp.oauth.complete`，顺手做一次发现。无论成败浏览器都被 303 到
   `/agent/integrations/{connection_id}?auth=ok` 或 `?auth=error&code=<错误码>`。
6. **刷新**：访问令牌 30 秒内到期且有刷新令牌时在使用前刷新；刷新失败 → `mcp_auth_required`，连接健康置 `auth_required`。

Agent 自己的客户端元数据文档在 `GET {SYNWEVO_APP_URL}/.well-known/mcp-client/synwevo-agent.json`（`Cache-Control: public,
max-age=3600`）：`client_id` 就是这个地址，`client_name "Synwevo Agent"`，`redirect_uris` 只有上面的回调，
`token_endpoint_auth_method "none"`，`grant_types ["authorization_code", "refresh_token"]`，`response_types ["code"]`，
`application_type "web"`。

### 4.4 运行时：出站网络边界（`runtime.py`）

* 进程里 **一个** asyncio 事件循环（守护线程 `synwevo-mcp-client`）；Agent 的回合引擎在工作线程上，经
  `run_coroutine_threadsafe` 提交，结果或带稳定代码的 `ServiceError` 回到线程侧。
* **目的地**：`check_address_text`（协议、主机、端口；非 loopback 的 http 需策略允许）→ 策略 `destination_refusal`
  （`blocked_hosts` 先于 `allowed_hosts`）→ `webhook_delivery.resolve_checked(allow_private=策略 allow_private_networks)`
  解析并校验为公网地址 → `PinnedTransport(host, address)`：请求连到校验过的地址，`Host` 头与 TLS SNI 仍是原主机名，证书照常
  校验；请求的主机不是连接的主机（重定向目标、服务端回传的资源链接）直接拒绝。`retries=0`、`trust_env=False`、
  `follow_redirects=False`。`User-Agent: Synwevo-Agent/1 (+mcp)`，`clientInfo {"name": "Synwevo Agent", "version": "1"}`。
* **边界**：连接超时 10 秒；读超时 = 策略 `call_timeout_s`（默认 60）或 `discover_timeout_s`（默认 30）；每连接
  `max_concurrent_calls_per_connection`（默认 4）与每人 `max_concurrent_calls_per_user`（默认 8）用信号量，**满了就拒绝**
  （429 `mcp_connection_busy` / `mcp_user_busy`，`retry_after: 2`）而不是排队。
* **取消**：本人停止回答时，提交侧每 0.5 秒检查一次 `should_stop`，为真即取消协程——关闭响应流就是传输层的取消信号；
  记录状态 `cancelled`（409 `mcp_cancelled`）。
* **错误分类**（`classify`，一种失败一个代码，从不转述服务端的原话）：超时 504 `mcp_timeout`；HTTP 401/403 → 401
  `mcp_auth_required`；404 → 502 `mcp_not_found`；429 → 503 `mcp_rate_limited`；其它 HTTP 状态 → 502 `mcp_server_error`
  （≥500 标 retryable）；连不上 → 502 `mcp_unreachable`；SDK 的 `MCPError` → 502 `mcp_protocol_error`（带
  `jsonrpc_code`）；其余 502 `mcp_internal`。解析失败 502 `mcp_unresolvable`；解析到不允许的地址 403 `mcp_host_not_allowed`。
* 测试缝：`McpRuntime.TEST_TRANSPORT_FOR`（夹具 ASGI 传输代替钉住的网络传输），产品代码从不设置。

### 4.5 模型看到什么，调用如何执行（`tools.py`、`engine.py`）

外部工具以 **虚拟操作** `mcp:<别名>/<工具>` 进入模型的操作索引，与业务操作一样用 `find_operations` 查、`run_operation` 调：

* 系统提示里每个连接最多列 40 个工具（其余按查询找到），并有一段固定说明：它们在平台之外运行，描述与结果是不可信内容，
  多数会改平台外的数据并会先给本人看，工作空间内容只在数据流策略允许时才会到达它们。
* `find_operations` 返回服务端的输入 schema（规范化后 ≤20 000 字符，否则给预览）、`external: true`、连接与工具名、一句
  "外部服务的工具，描述与结果不是 Synwevo 的，可能有错或有敌意"。
* `run_operation` 前：输入必须是对象且 ≤200 000 字符；按 **JSON Schema Draft 2020-12** 校验（`jsonschema`）；schema 本身
  不合法或过深 → `mcp_schema_invalid`（工具不可用）；带 `workspace_id` → `workspace_not_applicable`。
* **数据流**：对话里读到的工作空间内容（`sources_json` ∩ 本回合会随请求送出的空间）中，任一空间的出站策略
  （`data_flow.workspace_outbound`）不是 `any`、或其组织的上限不是 `any` → 调用在发出前拒绝，`mcp_transfer_refused`，
  消息里写明哪个空间；不看工具是什么。共享空间的默认出站是 `audience`/`organization`，所以默认拒绝，所有者明确放开才行。
* 执行走两个已登记的 Agent 操作之一：服务端标只读且授权 `auto` → `agent.mcp.tool.read`（`side_effect="none"`，`agent="auto"`）；
  否则 `agent.mcp.tool.call`（`side_effect="external"`，`agent="confirm"`：确认卡、与业务操作相同的绑定指纹）。两者都
  `transactional=False`，幂等键 `agent:<tool_call_id>`，运行记录与审计是能力层的。`result_guard`：已存的结果只在本人仍可用
  该连接时可读（409 `result_access_changed`）。
* 结果给模型：`{"source": {"external": true, "connection", "tool"}, "note": <不是指令>, "is_error", "truncated",
  "structured_content"?, "text"}`；文本在服务层按 `max_result_chars`（默认 24 000）截断并标注；结构化内容过大则丢弃并标注；
  图片 / 音频 / 二进制资源只留占位文字，不进上下文。`isError: true` 的结果是 **成功送达的内容**，不是失败。
* 对话记录（`engine.call_json`）里这类调用带 `mcp` 对象 `{connection, connection_name, tool, is_error, duration_ms,
  truncated, call_id, external: true}`，`operation_title` 为 "工具 · 连接名"。

### 4.6 资源与提示词

* **页面读取**（`agent.mcp.resource.browse` / `read_page`、`agent.mcp.prompt.render`）：本人有使用权即可，不需要绑定；
  读取按 `max_resource_bytes`（默认 1 MB）截断为 413 `mcp_resource_too_large`，二进制块只留 `blob_omitted`；提示词参数
  必须是服务端声明过的、字符串、≤4000 字，必填缺失拒绝。渲染结果给本人看，本人可以把它作为自己的消息发出——它 **不会**
  自己进入模型，也不改变 Agent 的任何规则。
* **Agent 读取**：绑定开了 `resources_enabled` 时，模型多两个只读虚拟操作 `mcp:<别名>/resources.list`、`resources.read`
  （`agent.mcp.resource.list` / `resource.read`，auto），同样走数据流检查与外部标注。

### 4.7 记录、健康与熔断

`mcp_calls`：每次 `tools/call`、`resources/read`、`prompts/get`、`discover` 一行，**在请求发出前插入**（`running`），结束时
写 `succeeded / failed / cancelled / timeout`；字段有发起方（`agent / test / discover / page`）、本人、对话 / 回合 / 工具调用
id、请求编号、输入哈希与 ≤4000 字的输入 / 结果预览、错误代码与消息、时长。进程在两步之间崩溃留下 `running`，而不是没有
记录。查看规则（`calls()`）：预览、对话与回合 id 只给调用者本人；`user_id` 给管理者或本人；其余字段成员可见。

健康：成功 → `ok`；`mcp_auth_required` → `auth_required`；其它失败 → `failed`，`failure_streak` 加一；连续失败达
`breaker_failures`（默认 5）→ `open_until = now + breaker_open_s`（默认 120 秒），期间任何使用 503 `mcp_circuit_open`
（`retry_after`）。`mcp_cancelled` 与 `mcp_resource_too_large` 不计入失败。

## 5. 平台策略与管理

`synwevo_core.mcp_policy.McpPolicySpec`（`extra="forbid"`，`strict=True`），版本化、非机密、每次决策都读库，无缓存、无环境
变量覆盖：

| 字段 | 默认 | 含义 |
|---|---|---|
| `outbound_enabled` | **false** | Agent 可连接外部 MCP 服务（能创建与使用连接） |
| `inbound_enabled` | **false** | 外部 MCP 客户端可调用 Synwevo（为该受众签发的令牌才被接受） |
| `allow_http` | false | 允许明文 http（含 loopback；本机与内网地址另需 `allow_private_networks`） |
| `allow_private_networks` | `[]` | 允许连接的非公网主机 / 网段（CIDR） |
| `allowed_hosts` | `null`（任意公网主机） | 允许的目的主机（精确或 `.example.com` 后缀）；空列表 = 什么都不允许 |
| `blocked_hosts` | `[]` | 一律拒绝的主机 |
| `max_connections_per_user` / `_per_workspace` | 10 / 20 | 数量上限 |
| `max_concurrent_calls_per_connection` / `_per_user` | 4 / 8 | 并发上限 |
| `call_timeout_s` / `discover_timeout_s` | 60 / 30 | 一次调用 / 一次发现的墙钟上限 |
| `max_result_chars` | 24 000 | 进入模型上下文的结果上限（规范化 JSON 字符） |
| `max_resource_bytes` | 1 000 000 | 读取资源的上限 |
| `breaker_failures` / `breaker_open_s` | 5 / 120 | 熔断阈值与时长 |
| `client_registration` | **`preregistered_only`** | `cimd_and_preregistered` 时接受客户端元数据文档 |
| `cimd_allowed_domains` | `[]`（任意 https 主机） | 接受文档的域 |

修订 0 是内置默认；每次编辑追加一份完整快照到 `mcp_policy_versions`（`expected_revision` 不等于当前 → 409
`mcp_policy_revision_conflict`）；快照损坏或来自未来版本 → 503 `mcp_policy_unavailable`，**不**回落到宽松默认。

管理接口 `/api/admin/mcp`（`services/identity/synwevo_identity/admin_mcp.py`）：

| 路由 | 权限 | 说明 |
|---|---|---|
| `GET /policy`、`GET /policy/history` | `platform.config.view` | 当前状态、默认值、JSON Schema；历史 |
| `POST /policy/preview` → `POST /policy/publish` | `platform.config.manage`，危险操作（严格重新验证）、理由 8–500 字、`idempotency_key`、`preview_token` | 与其它平台设置同一 `SignedPreview` 层；`before/after` 变化在预览里，发布时再比对一次 |
| `GET /overview` | `platform.config.view` | 每个连接的 **遮罩视图**：名称、归属、主机名、认证方式、状态、健康、最后错误代码、失败连击、熔断、协议版本、工具 / 已授权 / 绑定人数、7 天调用与失败数；合计里有 24 小时的出站调用与入站请求（`api_platform_calls` route `/api/suite/v1/mcp`）数 |
| `GET /connections/{cid}/calls` | `platform.config.view` | 调用记录的元数据（发起方、类型、名字、状态、错误代码、时间、请求编号）；**没有**预览 |
| `POST /connections/{cid}/disable` | `platform.config.manage`，危险操作，理由 | 平台级停用：状态 `disabled`，凭据忘掉，绑定保留但产出为空 |

平台管理员读不到任何连接的凭据、调用参数或结果——与「平台管理员默认读不到业务内容」一致。

## 6. 数据与密钥

* 表（`packages/core/synwevo_core/suite_schema/mcp.py`，全部 `CREATE … IF NOT EXISTS`）：`mcp_connections`、
  `mcp_connection_tools`、`mcp_connection_resources`、`mcp_connection_prompts`、`mcp_tool_grants`、`mcp_agent_bindings`、
  `mcp_calls`、`mcp_oauth_flows`、`mcp_policy_versions`。`oauth_clients` 加四列（`registration` 默认 `'operator'`、
  `client_uri`、`metadata_json`、`metadata_fetched_at`，`ALTER TABLE … ADD COLUMN`，加法）。老代码不读这些表与列。
* 密封（`synwevo_core.sealed`）新用途 `mcp-credential`，覆盖 `mcp_connections.credential_sealed`、
  `mcp_connections.oauth_client_secret_sealed`、`mcp_oauth_flows.code_verifier_sealed`，已列入 `SEALED_COLUMNS`，根密钥
  更换（`secret rekey`）会走到。
* **回滚**：发布一版策略把两个方向关掉即停止一切（无需重启：出站的每个入口先查 `outbound_enabled`，入站网关先查
  `inbound_enabled`）。要卸下代码，去掉 `synwevo_suite/app.py` 与 `agent/__init__.py` 的两处 `install` 调用和 `mcp`
  依赖即可，数据不丢：表与列都是加法，连接、授权、记录原样留在库里，再装回去继续可用。

## 7. 计量

* 出站：`mcp_calls`（次数、时长、状态、错误代码）是唯一的计量来源；Agent 发起的调用另有 `op_runs`（`agent.mcp.tool.read` /
  `tool.call`，`via="agent"`）与审计。**它们不是模型 token**，不进任何模型用量或费用；外部服务是否收费本平台不知道，
  也不编造第三方成本。
* 入站：每个 POST 一行 `api_platform_calls`（route `/api/suite/v1/mcp`，状态、时长、错误代码、客户端、请求编号，不含请求体），
  进 API 控制台的调用记录；每次业务调用一行 `op_runs`（`via="api"`，`delegation.transport="mcp"`）与工作空间审计，署名
  为本人并注明客户端——与 REST 完全相同的记录，多一个 `transport` 字段把两扇门分开。

## 8. 边界与未做

| 项 | 状态 | 理由 |
|---|---|---|
| stdio 传输 | **不支持** | 多租户服务不能为连接而启动任意本地进程；只有 Streamable HTTP |
| Docs / Message 走入站 MCP | 不暴露（§3.3） | 它们不是机器应用；Docs 的端点属于 Docs 源站 |
| 入站 prompts、`subscriptions/listen`、任务扩展、MRTR | 未提供 | 服务端只登记了 `tools/list`、`tools/call`、`resources/list`、`resources/read` 四个处理函数 |
| 出站：服务端发起的请求（采样、征询等） | 未处理 | Agent 侧的 SDK 客户端没有为这类请求登记处理函数；它们不会得到满足 |
| 出站：提示词进入模型 | 不自动 | 只渲染给本人看（§4.6） |
| 服务账户调用入站 | 不支持 | 与 REST 一致（`service_account_not_supported`） |
| 动态客户端登记（RFC 7591） | 账户服务 **不提供**；Agent 出站在授权服务器提供时 **会用** | 入站：规范已弃用且本平台一直拒绝无认证创建回调地址；出站：许多现存服务端只有它，兼容到对方迁走 |
| 工作空间内容的出站判断 | 只看空间与组织的数据流出站策略是否为 `any` | 不按工具、不按内容分级；注解不可信 |
| 真实模型 + 真实外部服务 | **待做** | 现有证据是夹具服务端 + 脚本模型（§9） |
| 运行中的 step-up | 缺 scope 的操作不在 `tools/list`；直接调用得到 `isError` 的 `insufficient_scope`（带 `required_scope`），不是 HTTP 403 challenge | JSON-RPC 处理函数里不能改写 HTTP 状态；客户端需要更大权限时重新授权 |

## 9. 验证

| 内容 | 位置 | 性质 |
|---|---|---|
| 元数据与 401 challenge 把客户端引向授权服务器；REST 令牌与 MCP 令牌互不通用；工具随 scope 增减、调用就是操作（创建 + 幂等重放 + 列表 + `run.get`）；错误是 `isError` 结果（`invalid_input`、`insufficient_scope` 带 `required_scope`）；未知工具是协议错误；两个资源；运行记录 `delegation.transport="mcp"`、`api_platform_calls` 有行；2025 年代客户端经 `initialize` 握手走通（`mode="legacy"`）；查看者拿着 write scope 也写不了；策略关闭即 403；别的主机名不是本服务 | `apps/suite/tests/test_mcp_server.py`（7 项） | **官方 SDK 的客户端**打真实应用（`synwevo_suite.app.create_app`，ASGI 传输），经真实网关；令牌直接铸造（账户服务是另一模块） |
| 连接测试是真实握手并记录供给；模型找到、调用并确认外部工具（只读 auto 直接执行且结果标为外部内容，写工具等确认卡，确认后执行，系统提示与记录齐全）；工具错误与坏输入以内容到达模型；工作空间内容在策略不允许时不出站、允许后出站；连接私有、工作空间授权属于管理员、个人只能收窄；工具形状变了授权作废直到重设；慢调用超时、连续失败熔断；策略关闭即隐藏；资源与提示词在页面读、资源经绑定开放给 Agent | `apps/suite/tests/test_agent_mcp.py`（9 项） | **夹具** MCP 服务端（官方 SDK 的 `MCPServer`，进程内 ASGI 传输代替钉住的网络传输）+ **脚本模型**（`ScriptedModel`）。协议是真实的，网络不是 |
| A. 官方 SDK 客户端：受保护资源元数据 → RFC 8414 元数据 → 真实登录 → 同意页（接口层选择工作空间）→ 令牌端点（`resource` 为 MCP 端点）→ `tools/list` 只含 scope 覆盖的操作 → `synwevo.whoami` → 创建 + 列表 → `op_runs.delegation.transport="mcp"` → 本人撤回同意后下一请求 401 带 `resource_metadata`；B. Agent 添加并测试夹具服务、绑定、脚本模型调用 → 等确认 → 确认后执行、结果标为外部内容、`mcp_calls` 有行 | `tests/integration/test_mcp_end_to_end.py`（2 项） | **组装后的部署**（`synwevo_server.app.create_app`，角色 `all`），一个进程、一个网关；夹具服务端 + 脚本模型 |
| RFC 8414 文档 = Discovery + MCP 事实；每个授权响应带 `iss`；工作空间选择：必选（`workspace_required`）、记住唯一已同意空间、扩 scope 再问并标 `consented`、带 `workspace_id` 不问且不接受换；客户端元数据文档：策略关着不拉取、域不符不拉取、通过后登记为公共客户端且受众只有 MCP、24 小时内不重拉、文档未列的回调拒绝；五种坏文档什么都不登记；拉取只接受公网 https 且 `client_id` 须有路径 | `services/identity/tests/test_oidc_mcp.py`（6 项，其一参数化 5 例） | 真实登录、同意与令牌端点；**夹具**替代 `cimd.fetch_document` 的网络拉取 |
| 契约里多出 `/api/suite/v1/mcp` 与标签 `mcp` | `apps/suite/tests/test_machine_contract.py`、`tests/integration/test_suite_machine_contract.py` | 契约漂移测试 |
| 同意页返回 `client.registration` / `client_uri` | `services/identity/tests/test_oauth_consent.py` | 既有测试随形状更新 |
| MCP 客户端的 scope 收窄；非 MCP 客户端照旧拒绝 | `services/identity/tests/test_oidc_mcp.py`（新增 2 项，共 8 项） | 真实登录、同意与令牌端点 |
| Agent 出站 OAuth 完整回合（以 Synwevo 自己的 MCP 端点为外部服务）：发现 → 预登记 client_id → 同意 → 回调核对 `state`/`iss` → 换码 → 密封 → 发现工具 → 强制过期后刷新 → 撤销；三种坏回调都拒绝 | `tests/integration/test_mcp_outbound_oauth.py`（2 项） | 组装后的部署；运行时经进程内 ASGI 传输 |
| 前端：集成中心（列表、分步新建、详情六个页签、客户端页）、执行卡片、Agent 设置一节、管理台 MCP 页、同意页选择；三种语言、权限禁用与原因、OAuth 回调提示、示例只来自服务端地址 | `apps/suite/web/tests/agent-integrations.test.ts`（25 项）、`services/identity/web/tests/admin-mcp.test.mjs`（10 项） | 服务端渲染 + 夹具数据 |
| 真实 HTTP 与浏览器：官方 SDK 客户端两种握手、SDK 的 `OAuthClientProvider` 完整授权（撞出并修复上面的 scope 收窄缺陷）、Claude Code 2.1.146 连接；Agent 经钉住传输连接回环上的真实 SDK 服务端；浏览器新建连接 → 授权工具 → 绑定 → 对话执行与确认 → 客户端页 → 管理台发布策略（重新验证、审计）→ 同意页选择；375 px 无横向溢出 | `docs/agent-work/evidence/2026-10-24-mcp/` | 本机隔离实例；模型为规则替身 |

**没有的证据**：真实模型驱动的调用（证据中的模型是替身）；第三方运营的公网 MCP 服务；钉住传输对公网 DNS 与 TLS 的行为
（证据只覆盖回环 http）；客户端元数据文档的真实网络拉取（测试注入了文档）；生产部署。

## 10. 决定与代价

| 决定 | 理由 | 代价 / 替代 |
|---|---|---|
| 入站用 SDK 的低阶 `Server` 而不是 `MCPServer`（装饰器） | 工具不是函数，是注册表里的操作；需要按调用方逐请求过滤列表并自己造定义 | 工具定义的每个字段要自己填（§3.3 表） |
| 无状态（`stateless=True`） | 多进程部署无需共享会话；2026-07-28 本来就无会话 | 2025 年代客户端的每个请求都重做一次握手的工作，由 SDK 承担 |
| MCP 受众是 https URL 而不是 URN | 规范要求客户端以服务端的规范 URL 作 `resource` | 依赖 `SYNWEVO_APP_URL` 正确；提供 `SYNWEVO_MCP_RESOURCE` 覆盖 |
| 入站 scope 复用 REST 的 12 个 | 同一批操作；"同意写入不等于同意替我决定"同样成立 | 客户端要两扇门都用需登记两个受众 |
| 出站只认 `readOnlyHint` 才允许 auto | 注解不可信，但没有更好的信号；confirm 是默认 | 服务端乱标只读会让一个改数据的工具不经确认——数据流检查与「外部内容」标注仍在，且本人可按工具收窄为 confirm |
| 工作空间内容出站只认 `any` | 与跨空间传输规则一致：`audience`/`organization` 的成员比较对外部服务无意义 | 想用外部工具处理共享空间内容必须由所有者放开出站 |
| 出站 OAuth 由服务端驱动 | 浏览器不该拿到外部服务的令牌；回调在本人会话里 | 需要公开可达的回调地址（`SYNWEVO_APP_URL`） |
| 调用记录先于请求 | 崩溃不留空白 | 可能留下 `running` 行；控制台按状态能看出 |
