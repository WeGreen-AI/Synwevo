# Model Context Protocol（MCP）：设计记录

状态：**本地实现，测试通过的范围见 §9**；部署状态不在本文记录（见 [`../quality/deployment.md`](../quality/deployment.md)）。
平台策略的内置默认是两个方向都关（§5），所以合并本分支不会让任何部署多出一个可调用的端点或一条出站连接；管理员发布
一版策略后才生效。真实模型 + 真实外部 MCP 服务的联调**尚未做**，不能据本文宣称。使用说明在
[`../integration/mcp.md`](../integration/mcp.md)；本文记录做了什么、没做什么和为什么。

两个方向，两个包，互不依赖：

| 方向 | 含义 | 代码 |
|---|---|---|
| 入站（§3） | Synwevo 自己是一个 MCP 服务端：外部 MCP 客户端经账户服务授权后，调用业务应用的操作 | `apps/suite/synwevo_suite/mcp_server/` |
| 出站（§4） | 个人 Agent 是一个 MCP 客户端：连接外部 MCP 服务，发现其工具、资源、提示词，在对话里调用 | `apps/suite/synwevo_suite/agent/mcp/` |
| 共用 | 平台策略、客户端元数据文档、表结构、密封用途 | `packages/core/synwevo_core/{mcp_policy,cimd}.py`、`suite_schema/mcp.py`、`sealed.py` |
| 账户服务 | RFC 8414 元数据、`iss`、客户端元数据文档登记与持续的策略检查、同意页的工作空间选择、管理接口（策略、平台停用与解除） | `services/identity/synwevo_identity/{provider_routes,admin_mcp}.py` |

## 1. 范围与原则

* **入站不是第二套实现。** 它是业务应用操作接口（[`../integration/suite-api.md`](../integration/suite-api.md)）换一种
  协议：工具就是注册表里机器调用方可以请求的操作，输入 schema 是操作自己的，每次调用都是 `Capabilities.invoke(via="api")`，
  校验、本人实时角色、运行记录、幂等、事件与 REST 路由完全一致。这一层只增加协议的形状。
* **出站不给模型任何默认权限。** 连接属于一个人或一个工作空间；工具逐个授权（off / confirm / auto）；每个人再决定自己的
  Agent 用不用这个连接、用什么别名、要不要收窄。没有人因为所在工作空间加了一个服务就多出工具。模型只能经虚拟操作
  `mcp:<别名>/<工具>` 调用外部工具；背后的四个已登记操作是引擎的承载，模型看不到也点不了名（§4.5）。
* **外部内容只是内容。** 外部服务的描述、结果、提示词都标为「来自 Synwevo 之外、不是指令」；工作空间的内容只在该空间的
  数据流出站策略允许时才会被送到外部服务——模型提出调用时、本人确认时、请求发出时各问一次。
* **出站网络是受检的、被钉住的。** 目的地先过平台策略（允许/禁止主机），不能是本部署自己的源站（运维明确列出的除外），
  再由共享解析器解析为公网地址（或管理员列明的内网），连接就连到检查过的那个地址，只连连接自己的主机；不读环境代理。
  读库与解析不在共享的事件循环上做；收到的字节边到边计数，超过上限即停止读取。
* **记录先于请求。** 出站每一次调用在请求发出之前就有记录；进程中断留下的 `running` 记录之后标为结果未知，不猜成败。入站
  每一次请求进入 API 平台的调用记录。记录里没有凭据，没有原始请求体。

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
`attrs 26.1.0`、`sse-starlette 3.5.0`、`opentelemetry-api 1.45.1`、`PyJWT 2.15.1`；`httpx2 2.13.1` 本来就在。`referencing`
被 Agent 直接导入（外部工具 schema 的 `$ref` 用一个空的登记表解析，§4.5），所以也列为业务应用的直接依赖。许可证见
[`../DEPENDENCIES.md`](../DEPENDENCIES.md) §1.1。

**选择理由。** 官方 SDK 是唯一同时服务新旧两代修订的实现，而 Agent 要连接的外部服务两代都有；自己实现 JSON-RPC +
SSE + 两代握手不是本项目的价值所在。代价：多了十个依赖；`anyio`/SSE 的服务端任务组需要在进程里启动一次（§3.5）。

## 3. Synwevo 作为 MCP 服务端（入站）

### 3.1 位置与元数据

| 项 | 值 |
|---|---|
| 端点 | 工作空间源站 `{SYNWEVO_APP_URL}/api/suite/v1/mcp`（`synwevo_core.http.MCP_PATH`）：消息只走 `POST`；同一路径上的第二个路由对其它方法答 405（§3.5） |
| 规范 URI / 令牌受众 | `synwevo_core.oidc.mcp_resource()`：`SYNWEVO_MCP_RESOURCE`，否则 `{SYNWEVO_APP_URL}/api/suite/v1/mcp`，都经 `canonical_resource_uri` 规范化——scheme 与主机小写、去掉默认端口与片段、路径末尾不留 `/`（空路径写作 `/`），与客户端的 URL 解析器（SDK 与元数据文档用 pydantic 的 `AnyHttpUrl`）得出同一个字符串，所以令牌受众、元数据里的 `resource` 与客户端送回的值一致（`https://Workplace.Example.com:443` 不会变成一个没有客户端能写出的受众）。运维登记的受众若指向本 MCP 服务，保存与比较时同样换成规范形式（`oidc.registered_audiences`），其它受众原样。两个设置都没有时是 `urn:synwevo:platform:mcp:unconfigured`——没有客户端会用它申请令牌，受众检查因此不可能误通过 |
| 受保护资源元数据（RFC 9728） | `GET {SYNWEVO_APP_URL}/.well-known/oauth-protected-resource/api/suite/v1/mcp`：`resource`、`authorization_servers=[issuer]`、`scopes_supported`（`oidc.MCP_DEFAULT_SCOPES`：10 个业务 scope，五个应用各自的 read 与 write；`extract.decide`、`approvals.decide` 不默认宣告，客户端需明确申请）、`bearer_methods_supported=["header"]`、`resource_name="Synwevo MCP"`；用 SDK 的 `ProtectedResourceMetadata` 模型生成；`Cache-Control: public, max-age=300`、`Access-Control-Allow-Origin: *` |
| 角色 | 元数据路径与 Agent 的客户端元数据文档只由 `portal` 角色回答；其它角色答「MCP 服务在工作空间源站上」。生产网关把 `workplace.synwevo.com` 整个转给 portal 容器，不需要新的 location |
| 受众与 REST 的关系 | 同一批 scope（`MCP_SCOPES = SUITE_API_SCOPES`），不同受众：REST 的令牌（`urn:synwevo:platform:suite`）打不开 MCP（`invalid_audience`），反之亦然。客户端想两者都用，就登记两个受众并按资源分别申请令牌 |

### 3.2 谁能调用

网关在解析任何 JSON-RPC 之前决定（`MachineAuth.verify_mcp`，`packages/core/synwevo_core/machine.py`），对这个路径上的
任何方法都一样；顺序即下表：

| 检查 | 拒绝 |
|---|---|
| `Authorization: Bearer` 存在 | 401 `unauthorized` |
| 策略 `inbound_enabled` | 403 `mcp_inbound_disabled` |
| 不是 Docs 的 `dw_` 密钥 | 401 `wrong_credential` |
| 令牌存在、未过期、未撤销 | 401 `unauthorized` |
| 令牌受众 == `mcp_resource()` | 401 `invalid_audience`（`details.expected` 给出该带的 `resource`） |
| 其余与 REST 共用 `_suite_delegation`：客户端未停用（401 `client_unavailable`）；自描述登记的客户端仍被策略接受（401 `client_unavailable`，`reason: cimd_not_allowed`，§3.6）；属于 `platform` 应用（403 `wrong_audience`）；是本人授权而非服务账户（403 `wrong_credential` / `service_account_not_supported`）；持有业务 scope 且客户端仍登记它（403 `wrong_credential` / `forbidden` + `scope_withdrawn`）；账户可用（401 `unauthorized`）；绑定了工作空间（403 `no_workspace`）；空间与组织可用、本人仍是成员（403 `forbidden`） | 见左 |

**Challenge**（`http.mcp_challenge`）：每个 401 / 403 都带 `WWW-Authenticate: Bearer …, resource_metadata="<元数据地址>"`
（RFC 9728 §5.1）。其余内容决定客户端下一步做什么——官方 SDK 的客户端在 401 上重新授权，在 403 + `insufficient_scope`
上升级权限——所以错误码只写在那一步有用的地方：

* 请求没有出示 bearer 令牌：不写 `error`（RFC 6750 §3.1）；
* 401：`error="invalid_token"` 与 `error_description="<代码>"`——令牌过期、撤销、受众不对，重新授权能解决；
* 403：只有拒绝方给出了能申请到的 scope 时才写 `error="insufficient_scope", scope="…"`；其它 403（策略关闭、不是
  `platform` 应用的客户端、服务账户、没有工作空间、空间或组织不可用、已不是成员）不写错误码，因为新令牌或更多 scope
  都改变不了结果。网关里写它的只有一种情形：令牌没有一个还能用的业务 scope（只有身份 scope，或登记已撤回它持有的全部
  业务 scope），而重新授权这个客户端能拿到一些——`scope` 是 `MCP_DEFAULT_SCOPES` 中该客户端登记列出的那些
  （`oidc.mcp_grantable_scopes`：客户端登记了本 MCP 受众时它登记的 MCP scope）；一个都拿不到就不写。工具调用缺 scope
  的 403 见 §3.4。

challenge 只在头里：错误正文的 `details` 不带 `www_authenticate`。

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
| `outputSchema` | 信封 `{run_id, operation, status, output, replayed, workspace_id, truncated}`；`output` 是 `anyOf: [操作声明的输出 schema, {preview, note}]`——后者是结果过大时的截断形状（§3.4）；操作没有声明输出 schema 时 `output` 不限 |
| `annotations` | `readOnlyHint` = 副作用为 none；`destructiveHint` = 有副作用且 id 以 `.delete/.archive/.cancel/.withdraw/.revoke/.close` 结尾；`idempotentHint` = 只读，或契约要求必带 `idempotency_key`——MCP 的意思是「同样的参数再调一次没有额外效果」，客户端据此自动重试，可选键的写操作不带键重试会再执行一次，所以不标；`openWorldHint` = 副作用为 external |
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
   的操作对应的 scope（缺了按第 4 步的规则拒绝）。
3. 其它名字先查注册表：不存在 → JSON-RPC `-32602`（协议错误，不是结果）。
4. **scope 在分派之前、由端点检查**（`McpEndpoint._scope_refusal` → `McpTools.scope_refusal`）：一个 `tools/call`，所调操作需要的
   scope 与 `reaches` 有令牌没有的（`synwevo.run.get` 是所读运行的操作需要的 scope，只对本调用方看得到的运行检查）→
   **HTTP 403**，正文是这条请求的 JSON-RPC 错误：`-32602`，`data: {code: "insufficient_scope", required_scope, scopes, request_id}`。
   重新授权这个客户端能拿到所缺 scope 时（登记了本 MCP 受众且登记列出它们），challenge 为
   `error="insufficient_scope", scope="<已持有 ∪ 所需>"`——连已持有的一起列出，客户端升级后不会丢掉别的工具的权限；拿不到
   时不写错误码，`data.reason = "scope_not_registered"`，免得客户端每次调用都把本人送去一次结果不变的授权。官方 SDK 的
   客户端把这个正文当作该请求的错误抛出（`MCPError`，`data` 同上）。只检查 SDK 本来会接受的请求：Host / Origin、
   Content-Type、`Accept` 同时含 JSON 与 SSE、请求体 ≤4 MiB，以及 2026-07-28 的路由头与正文一致、版本可服务——不满足的由
   SDK 答 400 / 406 / 413 / 421，不发 challenge。这样的请求在 `api_platform_calls` 记为 403 `insufficient_scope`，不进
   `op_runs`。处理函数里同一检查仍在（`isError` 结果），只为从别的途径到达的请求兜底。
   之后依次：应用可用（`app_unavailable`）、`idempotency_key` / `correlation_id` 类型、契约要求必带而缺
   （`idempotency_key_required`）；幂等键按客户端命名空间存储（`api:<客户端哈希>:<键>`，与 REST 同一规则）；然后
   `Capabilities.invoke(op, args, user_id, workspace_id=令牌的空间, via="api", idempotency_key, correlation_id, principal,
   scopes, delegation={"kind": "oauth_client", "client_id", "grant", "transport": "mcp"})`。
5. 结果：`structuredContent` 是信封，`content` 的文本块是同一份 JSON（给不读结构化内容的客户端）。超过 400 000 字符
   （规范化 JSON）时不静默丢弃：带 `output` 的结果（操作信封、读回的运行）保留其余字段，只把 `output` 换成
   `{preview, note}`——`preview` 是 `output` 的 JSON 文本在上限内能放下的最长前缀——并标 `truncated: true`，所以仍符合声明的
   `outputSchema`，仍写明 `run_id`（一次已经执行的写入必须认得出来）；没有 `output` 的结果，或其余字段本身超过 200 000
   字符时，整个结果换成 `{preview, note, truncated: true}`。`_meta["com.synwevo/request_id"]` 是本次请求编号。
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
* `json_response=False`：请求的 `Accept` 必须**同时**含 `application/json` 与 `text/event-stream`，只写其一 SDK 答 406。
  回答 JSON 还是 SSE 由 SDK 决定，不看 `Accept`：2026-07-28 的请求，处理在 15 秒内结束且没有发通知 → `application/json`
  （带按规范映射的 HTTP 状态）；否则改为仅限本次请求的 SSE，每 15 秒一行 keepalive。2025 年代（握手）的请求一律以 SSE
  回答。
* DNS 重绑定保护（SDK 的 `TransportSecuritySettings`）：只回答 `Host` 为工作空间源站或 MCP 规范 URI 的主机（按规范形式：
  小写、无默认端口——`Host` 与 `Origin` 就是这样到达的；`APP_URL` 是本机地址时再加 loopback 名字）；其它主机名不是本服务。
* SDK 的处理函数任务组必须在进程里进入一次：`new_api` 的 lifespan 没有启动钩子，所以首个请求时进入、由 `shutdown_hooks`
  退出（`McpEndpoint._ensure_running` / `close`）。
* 请求体上限 4 MiB（SDK 答 413）。端点为做 §3.4 的 scope 检查先读完请求体（不超过这个上限），再原样交给 SDK。
* **POST 之外的方法**（GET、HEAD、DELETE、PUT、PATCH……）由同一路径上的第二个路由回答（`asgi._OtherMethods`，登记在
  POST 路由之后、各外壳的 `GET /{full_path:path}` 兜底路由之前）：405、`Allow: POST`、`error.code = method_not_allowed`
  （HEAD 无正文）。没有 GET 流，无状态也就没有可供 DELETE 结束的会话。先前 GET 落进外壳的兜底路由答 404，2025 年代客户端
  打开监听流时会把它读成「会话已经没了」。谁在请求仍由网关先判：不带令牌的 GET 是 401。
* 网关（`infra/production/nginx.conf`）：**反向代理配置没有为 MCP 改动**。端点在工作空间源站（`workplace.synwevo.com`）上，
  网关把这个主机整个经 `location /` 转给 portal 容器；全局已是 `proxy_buffering off` / `proxy_request_buffering off`，所以 SSE
  响应逐条到达客户端；读超时 300 s，与机器 API 的 REST 请求相同。2026-07-28 没有长连接的 GET 流，不需要像协同编辑那样单开
  `location`。前面的 Cloudflare 对一个没有字节的源站响应有自己的等待上限（非企业套餐约 100 s），它同样作用于 REST；耗时的
  业务操作本来就是异步的——工具立即返回 `run_id`，客户端用 `synwevo.run.get` 轮询，单个请求不长时间占着连接。

### 3.6 账户服务的配套

| 项 | 实现 |
|---|---|
| RFC 8414 | `GET {SYNWEVO_ISSUER}/.well-known/oauth-authorization-server` 与 OIDC Discovery 同一份文档（`oidc.discovery`）；`code_challenge_methods_supported=["S256"]`；`authorization_response_iss_parameter_supported: true`；`client_id_metadata_document_supported` 取自策略 `client_registration == "cimd_and_preregistered"` |
| RFC 9207 `iss` | `/oidc/authorize` 的每个重定向（成功、错误、同意页之后）都带 `iss=<issuer>` |
| MCP 客户端的 scope 收窄 | 按 MCP 授权规范的 scope 选择规则，标准客户端会申请元数据宣告的全部 scope；以前 `authorize` 拒绝任何未登记的 scope，于是只登记了部分 scope 的客户端永远授权不了（官方 SDK 的 `OAuthClientProvider` 实测撞上）。现在对登记了 MCP 受众的客户端，申请收窄到登记范围（RFC 6749 §3.3），令牌响应写明实际 scope；收窄后不剩登记范围内的业务 scope 才拒绝。其他客户端照旧拒绝未登记 scope |
| 客户端元数据文档（`synwevo_core.cimd`） | `client_id` 形如 https URL（有主机、有路径、无 query/fragment/凭据）时——未登记的，以及已登记且 `registration='cimd'` 的——**每次** `authorize` 都经 `ClientMetadataDocuments.resolve`：先问策略（机制开着且主机在允许的域内，`cimd_allowed`；否则 400 `invalid_client`）；已被停用 → 拒绝；登记不到 24 小时 → 直接用；否则重新拉取，按文档此刻的内容替换登记（文档删掉的回调从此不被接受）；拉取或校验失败 → `invalid_client`，旧登记不再可用，下次授权再试；运维手工登记的同名 id 以手工登记为准，不受文档策略约束。**拉取只为已登录的人做**：需要拉取而请求背后没有有效的中心会话时，不拉取、不写库，303 去登录，登录后回到同一个授权请求（审计 `oidc.client_metadata_document` 记 `requested_by`）；匿名请求因此不能让账户服务为它点名的每个 URL 拉取并存一份文档。拉取走 `webhook_delivery.resolve_checked` + `pinned_opener`：https、公网地址、不跟随重定向、64 KiB、10 秒、`Content-Type` 为 `application/json`（或缺省）。校验：`client_id` 与地址相等；`redirect_uris` 1–10 条且每条过 `_check_redirect`（非本机必须 https）；`token_endpoint_auth_method` 只能是 `none`（本平台不给没见过的客户端发密钥）；`grant_types` 含 `authorization_code`；`client_name` 去控制字符、≤120 字；`client_uri` 只在与 `client_id` 同源（https、同主机同端口、无凭据）时保留，否则丢弃——文档可以自称任何主页。通过后登记为 `platform` 应用的 **公共** 用户客户端，scope `openid profile email offline_access` + 12 个业务 scope，受众只有 `mcp_resource()`；文档原文（≤64 KiB）与拉取时间存入 `oauth_clients.metadata_json / metadata_fetched_at`，登记与这两列在同一事务里写。**策略撤回即失效**（`cimd.withdrawn_by_policy`）：机制关掉、或主机不再在允许的域内时，登记与已签发的令牌原样留着（重新允许即恢复），但在下一次使用时而不是到期时失效——令牌端点 401 `invalid_client`，userinfo 401 `invalid_token`，同意接口 410 `consent_expired`，MCP 网关 401 `client_unavailable`（`reason: cimd_not_allowed`） |
| 同意页 | `GET /api/auth/oauth-consent/{rid}` 多返回 `client.registration`（`operator` / `cimd`）、`client.client_uri`、`workspace_choices`（`null`，或 `[{id, name, role, consented}]`）。何时给选择：客户端属于 `platform` 应用、申请了需要同意的 scope、且 `authorize` 没带 `workspace_id` 时——本人此前对该客户端只在一个在用空间同意过且覆盖所申请 scope → 直接用它；只有一个可用空间 → 直接用；否则列出让本人选。`POST …/approve` 接受 JSON `{workspace_id}`；给了选择而没选是 400 `workspace_required`。同意页（`services/identity/web/src/pages/OAuthConsent.tsx`）渲染单选列表，标出已授权过的空间，未选择时「批准」不可用；`cimd` 客户端显示「这个客户端用它自己发布在 {host} 的文档描述自己。Synwevo 读取并检查了这份文档，但没有核实是谁在运营它。」，`{host}` 是 `client_id` 的主机——账户服务拉取文档的地方，唯一核对过的来源事实；不用 `client_uri`（evil.example 上的文档可以写 `https://claude.ai`） |
| 管理接口 | §5 |

## 4. Agent 作为 MCP 客户端（出站）

### 4.1 连接与归属（`connections.py`）

| 项 | 规则 |
|---|---|
| 归属 | `owner_kind='user'`（本人管理、只有本人可用）或 `'workspace'`（所有者 / 管理员管理，成员可用；空间须 active） |
| 看不到 = 不存在 | 非成员对工作空间连接、他人对个人连接：读与管理一律 404 `mcp_connection_not_found`（管理操作先查使用权，再查管理权）；成员要管理：403 `mcp_manage_forbidden` |
| 地址 | http(s) 绝对地址，无片段、无账号口令；明文 http 一律需策略 `allow_http`（loopback 也一样，保存与使用同一规则）；主机过策略（`mcp_host_blocked` / `mcp_host_not_allowed`）；不是本部署自己的源站（403 `mcp_destination_is_self`，§4.4）；≤2048 字符 |
| 数量 | 每人 `max_connections_per_user`（默认 10）、每空间 `max_connections_per_workspace`（默认 20）：409 `mcp_connection_limit` |
| 认证 | `none`、`bearer`（令牌密封，`credential_hint` 只留末四位）、`oauth`（§4.3；可填预登记的 `client_id` 与可选 `client_secret`，密封） |
| 改地址 | 新地址是新服务，旧服务得到的一切都不带过去：已发现的工具、资源、提示词标为移除；**工具授权删除**；所有人的绑定关掉（`enabled`、`resources_enabled` 置 0，别名与个人收窄保留）；bearer 令牌、OAuth 令牌组、OAuth 客户端（预登记的 id 与密钥、动态登记）忘掉，进行中的授权流程删除，刷新租约清空；健康与熔断清零，健康置 `auth_required`（`none` 认证为 `unknown`）——除非同一次修改给新服务带上了自己的凭据（新的 bearer 令牌，或预登记的 OAuth 客户端）。审计 meta 记 `credential_forgotten`。之后：管理者给新令牌或重新认证 → 测试（个人连接的这次发现按首次处理，每个可用工具得到 `confirm`；工作空间连接的工具等管理员授权）→ 每个人自己重新启用绑定。改地址时还在进行的测试，结果不记录（409 `mcp_address_changed`）；对旧服务的健康判断不记到新地址上 |
| 删除 | 软删除，密封列清空，绑定与进行中的授权流程删除；记录保留 |
| 停用 | 所有者的 `status='disabled'`：任何使用 409 `mcp_connection_disabled`；所有者可以再启用 |
| 平台停用 | 平台管理员的决定，记在 `platform_disabled_at` / `platform_disabled_by`（§5），与所有者的开关分开：启用、测试 / 刷新、认证（`oauth.start` 在发出任何请求之前就拒绝）、调用、读资源、渲染提示词、改地址、改认证方式或凭据都 409 `mcp_platform_disabled`，Agent 看不到它的工具与资源。所有者仍可查看详情与调用记录、改名称与说明、设授权、改自己的绑定（停用期间没有产出）、关闭、撤销凭据、删除。这些拒绝对应的写入都带 `platform_disabled_at IS NULL` 条件，检查之后才落下的停用同样成立 |

每次写入都写审计（`suite.agent.mcp.*`，见 `common/audit_actions.py` 与账户侧 `app_actions.py` 的标签），meta 只有
主机名、认证方式、改了哪些字段，没有地址路径、没有凭据。添加与修改连接带 `result_guard`：页面以同一幂等键重试时拿到
当时写入的回执（本人仍可用该连接时），不会再写一次。

### 4.2 发现、授权、绑定

**发现**（`test` / `refresh`）是真实握手：SDK 客户端连上去读服务端能力，再按能力分页读 `tools/list`、`resources/list`、
`prompts/list`。每类最多 500 条（`runtime.MAX_LISTED`）：更长的列表只保留前 500 条，被截的类别记在 `Discovery.truncated`
并写一条警告日志——**连接、页面与调用记录都不显示这一点**，此前记录过、这次落在 500 条之外的工具被标为移除。同一个
cursor 再次出现、或连续 20 页为空 → 502 `mcp_protocol_error`（不是会结束的列表）；其余不结束的列表由字节上限或发现的期限
终止，失败有自己的代码，不会把截短的列表当成全部。

存下的内容有界（`_record_discovery`）：工具名 ≤128（否则跳过）、标题 ≤200、描述 ≤4000、输入 / 输出 schema 存为 JSON 后
各 ≤20 000 字符（与 Agent 给模型看的 schema 上限相同）、注解只留四个提示与标题；资源与提示词的描述 ≤2000、MIME ≤200、
提示词参数 ≤50 个（参数描述 ≤500）；`serverInfo` 只留名称、标题、版本、描述、网址；能力只留 tools / resources / prompts /
logging / completions，选项超过 1000 字符只记「有」；`instructions` ≤4000。一个连接的全部供给合计 ≤512 KiB（UTF-8；工具在先，
再资源、再提示词，各按服务端顺序，放得下就整条保留）。每个工具另存一个 **schema 哈希**（`tool_hash`：名字 + 输入 schema +
输出 schema + `readOnlyHint` + `destructiveHint`，按服务端发布的原样计算——描述变了不算，形状或只读提示变了才算）。服务端
不再列出的工具标 `removed_at`，再出现时恢复。协议版本、`serverInfo`、能力与 `instructions` 存在连接上。

**不可用的工具**（`mcp_connection_tools.unusable_code`）：输入或输出 schema 不是 JSON 对象 → `mcp_schema_invalid`；超过 20 000
字符 → `mcp_schema_too_large`；供给已经没有空间 → `mcp_offer_too_large`（只留名字与提示；超出的资源只留地址与名称，提示词
只留名字与参数名）。它们存一个任何校验器都不接受的占位 schema `{"type": "unusable"}`——没读 `unusable_code` 的代码也不能在
未检查的输入上运行它——只能设为 `off`（其它模式 409 `mcp_tool_unusable`，带原因与上限），不进任何 Agent。schema 缩回来
是新形状，原授权 stale；只因供给空间变化而先不可用、后可用的工具，保留原授权。

发现结果只记到发起时的那个地址：读到回答时连接已经改了地址 → 409 `mcp_address_changed`，什么都不记；回答到了但记录
失败 → 调用记录以 `mcp_record_failed`（500，本方的失败，不计入服务端健康）结束。

**授权**（`mcp_tool_grants`，连接级，管理者设置）：

| 模式 | 含义 | 限制 |
|---|---|---|
| `off` | Agent 不得使用 | — |
| `confirm` | 可用，但每次调用先给本人看确认卡 | 工具须可用（`mcp_tool_unusable`） |
| `auto` | 不经确认执行 | 只允许服务端标 `readOnlyHint: true` 的工具（409 `mcp_auto_requires_read_only`）；注解是不可信提示（§2.4），所以 auto 仍受 §4.5 的数据流检查与结果标注 |

授权钉在设置时的 schema 哈希上：服务端改了工具的形状，授权变 **stale**，有效模式按 `off` 算，直到有人再看一眼并重新设置
（工具页签对 stale 的授权给出「按原模式重新授权」）。个人连接首次发现时每个可用工具自动得到 `confirm`（本人看自己的服务；
改地址后的首次发现同样如此），之后新出现的工具不自动获得任何授权；工作空间连接在管理员决定之前没有任何授权。

**绑定**（`mcp_agent_bindings`，个人级）：本人决定自己的 Agent 用不用这个连接，别名（slug，≤24 字符，本人范围内唯一，
409 `mcp_alias_taken`），是否把资源开放给 Agent（默认不开），以及按工具的个人模式——只能是 `off` 或 `confirm`，即只能收窄
连接级授权，不能放宽。

`effective_tools(user)` 是 Agent 看到的最终集合：策略 `outbound_enabled` 为真 ∧ 绑定启用 ∧ 连接 active、未被平台停用且本人
仍可用 ∧ 工具可用 ∧ 授权不是 off 且不 stale ∧ 个人模式再收窄。顺序确定（别名、工具）。

### 4.3 认证：Bearer 与 OAuth（`oauth.py`）

Bearer：本人粘贴令牌，密封保存（用途 `mcp-credential`），每次交换时解封进 `Authorization` 头，不进日志、预览或结果；只发给
它所给的那个地址（改地址即忘掉，§4.1）。

OAuth 按 §2.3 的流程，**由服务端驱动**——浏览器不接触外部服务的凭据：

1. `agent.mcp.oauth.start`（管理者；平台停用的连接 409 `mcp_platform_disabled`，在发出任何请求之前；连接须为 `oauth` 类型，否则
   409 `mcp_auth_kind_mismatch`；部署须有 `SYNWEVO_APP_URL`，否则 503 `mcp_oauth_unavailable`）。
2. **发现**：向服务地址 POST 一条 `server/discover`（2026-07-28 头与 `_meta`）。期望 401：从 `WWW-Authenticate` 取
   `resource_metadata` 与 `scope`；按 SDK 的候选地址读 RFC 9728 文档。**资源元数据必须指向这个服务**（RFC 9728 §3.3，SDK 的
   `check_resource_allowed`：与连接地址同源，且连接的路径在它所指路径之下；根路径的文档指向整个源站），否则 502
   `mcp_oauth_discovery_failed`（`reason: resource_mismatch`）——不然一个服务可以指向别的服务的资源与授权服务器，本人在那边授予
   的令牌会作为 bearer 回到它手里；通过后取第一个授权服务器，`resource` 用文档里的值。没有资源元数据的 2025-03-26 服务以自身
   源站为授权服务器、以连接地址的规范形式为 `resource`。再按候选地址读 RFC 8414 / OIDC 元数据：其 `issuer` 必须与构造这个
   well-known 地址的 issuer **逐字相同**（RFC 8414 §3.3）——有资源元数据时是它列出的授权服务器，原样比较，不去掉末尾的 `/`；
   没有时是服务的源站（只有这条路径上 `https://host` 与 `https://host/` 视为相同）；不同 → 502 `mcp_oauth_discovery_failed`
   （`reason: issuer_mismatch`），防止授权码、PKCE verifier 与客户端密钥被送到应答者挑选的令牌端点。元数据没有在
   `code_challenge_methods_supported` 里列出 `S256`（包括根本没有这个字段）→ 502 `mcp_oauth_unsupported`。服务端不要求认证
   （答 2xx/400/404/405/406）→ 409 `mcp_auth_not_required`；找不到元数据 → 502 `mcp_oauth_discovery_failed`。
3. **客户端身份**，按规范的优先级：本人预登记的 `client_id`（可带密钥，走 Basic）→ 此前对同一 issuer 动态登记过的 id →
   **本平台的客户端元数据文档**（授权服务器宣告支持且 `SYNWEVO_APP_URL` 是 https）→ 授权服务器提供 `registration_endpoint`
   时的动态登记（公共客户端；失败 502 `mcp_oauth_registration_failed`）→ 都没有则 409 `mcp_oauth_registration_unavailable`，
   提示先到对方登记并填写 `client_id`。**密钥只发给它所属的授权服务器**（`_auth_headers`）：预登记客户端自己的密钥，或在同一
   issuer（`issuers_match`）动态登记得到的密钥；授权服务器换了、或这次用的是元数据文档，旧密钥不发送。
4. PKCE S256、`state`（32 字节）、`resource`、发现得到的 `scope`——授权服务器的元数据列出 `offline_access` 时加上它，
   否则拿不到刷新令牌，访问令牌一过期连接就要本人重新授权；流程行（`mcp_oauth_flows`，code_verifier 密封）有效 600 秒；
   返回 `authorization_url`，页面把浏览器送过去。审计 `suite.agent.mcp.oauth.start`。
5. **回调** `GET {SYNWEVO_APP_URL}/api/suite/agent/mcp/oauth/callback`，在本人已登录的会话里：`state` 对应的流程须属于本人
   （404 `mcp_oauth_flow_not_found`）、未处理（409 `mcp_oauth_flow_decided`）、未过期（410 `mcp_oauth_flow_expired`）；
   `iss` 存在则必须逐字等于发现时的 issuer，授权服务器宣告会发而没发同样拒绝（400 `mcp_oauth_issuer_mismatch`）；授权服务器
   返回 `error` → 400 `mcp_oauth_denied`。然后在钉住的连接上换码（不跟随重定向；失败 `mcp_oauth_token_failed`），令牌组
   密封存到连接上，流程关闭，审计 `suite.agent.mcp.oauth.complete`，顺手做一次发现。无论成败浏览器都被 303 到
   `/agent/integrations/{connection_id}?auth=ok` 或 `?auth=error&code=<错误码>`。
6. **刷新**：访问令牌 30 秒内到期且有刷新令牌时在使用前刷新，**一次只有一个调用方刷新**（`Connections._refreshed`）：进程内
   每个连接一把锁，部署内一个写在行上的租约（`refresh_claim_until`，120 秒，条件 UPDATE 取得）。取得租约后再读一次——别的进程
   刚刷新过就用它的结果，不把已经用掉的刷新令牌再发一次；没取得的等待（每 0.2 秒看一次）并使用存下的新令牌，令牌还没真的
   过期时直接用旧的；等满 120 秒仍不成 → 429 `mcp_connection_busy`。用的是这次授权流程的客户端与 issuer。刷新失败 → 401
   `mcp_auth_required`（带 `cause`），连接健康置 `auth_required`，请求不在没有凭据的情况下发出。

**写入只落在同一个连接上。** 动态登记的结果、流程行、令牌组，都只在连接仍是授权开始时那个 OAuth 连接时写入（同一地址、
仍是 `oauth`、未被平台停用、未删除）：改地址、改认证方式、平台停用、删除会删掉流程，这些变化与撤销凭据都清掉刷新租约；换码或刷新之后的 `store_tokens`
要求这次授权的流程仍在（刷新还要求自己的租约仍在），否则 409 `mcp_connection_changed`（平台停用时为
`mcp_platform_disabled`）；平台停用或改地址之后才到的回调找不到流程（404 `mcp_oauth_flow_not_found`），不换码。

Agent 自己的客户端元数据文档在 `GET {SYNWEVO_APP_URL}/.well-known/mcp-client/synwevo-agent.json`（`Cache-Control: public,
max-age=3600`）：`client_id` 就是这个地址，`client_name "Synwevo Agent"`，`redirect_uris` 只有上面的回调，
`token_endpoint_auth_method "none"`，`grant_types ["authorization_code", "refresh_token"]`，`response_types ["code"]`，
`application_type "web"`。

### 4.4 运行时：出站网络边界（`runtime.py`）

* 进程里 **一个** asyncio 事件循环（守护线程 `synwevo-mcp-client`）；Agent 的回合引擎在工作线程上，经
  `run_coroutine_threadsafe` 提交，结果或带稳定代码的 `ServiceError` 回到线程侧。**会阻塞的事不在循环上做**：策略（读库）
  在提交之前于调用线程读取；目的地检查与 DNS 解析经 `asyncio.to_thread` 在 `synwevo-mcp-prepare` 线程池里做（线程数 = 共享
  解析器的槽数，16）；循环只搬字节，一个解析得慢的名字不拖住别人的调用、期限与取消。OAuth 的元数据、登记与令牌请求走同一个
  入口（`McpRuntime.http`），同样如此。
* **目的地**（`_destination`）：`check_address_text`（协议、主机、端口；明文 http 需策略 `allow_http`）→ 策略
  `destination_refusal`（`blocked_hosts` 先于 `allowed_hosts`）→ 不是本部署自己的源站（下一条）→
  `webhook_delivery.resolve_checked(allow_private=策略 allow_private_networks)` 解析并校验为公网地址 → 解析到的地址不是回到本机
  本部署端口的 loopback → `PinnedTransport(host, address)`：请求连到校验过的地址，`Host` 头与 TLS SNI 仍是原主机名，证书照常
  校验；请求的主机不是连接的主机（重定向目标、服务端回传的资源链接）直接拒绝。`retries=0`、`trust_env=False`。**重定向**：
  MCP 请求由 SDK 的 Streamable HTTP 客户端决定，它不看 `follow_redirects`，只跟随留在端点源站（同 scheme、主机、端口，或同主机
  默认端口的 http→https）且保持方法的重定向（POST 只有 307/308），其它不跟随、该消息失败；跟随时钉住传输照样只连连接自己的
  主机。OAuth 的元数据、登记与令牌请求不跟随任何重定向（`follow_redirects=False`）。`User-Agent: Synwevo-Agent/1 (+mcp)`，
  `clientInfo {"name": "Synwevo Agent", "version": "1"}`。
* **本部署自己的源站**（`is_own_origin`、`is_own_address`）：把 Agent 连回本部署，工作空间的内容会经工具结果回到模型，绕过原生
  操作所走的 Agent 处理策略与来源记录，所以默认拒绝（403 `mcp_destination_is_self`），保存时与每次使用时（含 OAuth 交换）同一
  规则。「自己的」指：`SYNWEVO_APP_URL`、`PUBLIC_URL`、`ISSUER`、`PORTAL_URL`、`SITE_URL`、`MCP_RESOURCE` 指向的源站，`apps`
  表里每个已登记应用的地址（Docs、Message），以及本进程监听的 loopback 端口（`SYNWEVO_PORT`，`SYNWEVO_HOST` 为 loopback 或通配
  时）。不是 loopback 的名字在任何端口、任何 scheme 上都算自己的（那里回答什么由网关决定）。loopback 名字（`localhost`、
  `*.localhost`、127.0.0.0/8、`::1`、未指定地址及其 IPv4 映射形式）都是同一台机器，**只看端口**：本部署的端口是自己的，别的端口
  是别的本机服务；没有配置 `SYNWEVO_APP_URL` 时不知道自己在哪个端口，任何 loopback 端口都算。解析之后再查一次：任何名字解析到
  loopback 地址且端口是本部署的，也算自己的。**明确放行**：策略 `allowed_hosts` 里有一条与该主机完全相同的条目（后缀规则
  `.example.com` 不算）。这是运维有意的决定（例如私有部署试连自己），含义有二：`allowed_hosts` 一旦不是 `null` 就是允许列表，
  其它所有目的地也要列出才能连；经它读到的本部署内容不走原生操作的 Agent 处理策略与来源记录。loopback 与内网另需
  `allow_private_networks`，明文 http 另需 `allow_http`。
* **边界**：连接超时 10 秒；读超时 = 策略 `call_timeout_s`（默认 60）或 `discover_timeout_s`（默认 30）。并发：每连接
  `max_concurrent_calls_per_connection`（默认 4）与每人 `max_concurrent_calls_per_user`（默认 8）是在循环上计数的进行中调用，
  每次接纳都与**当时**的策略比较（发布即对下一次调用生效，不必重启），**满了就拒绝**（429 `mcp_connection_busy` /
  `mcp_user_busy`，`retry_after: 2`）而不是排队。**收到的字节**边到边计数（`BoundedTransport`；请求 `Accept-Encoding: identity`，
  服务端仍压缩时 gzip / deflate 在同一上限下逐块解开再计数，其它编码、压缩体结束后的多余字节都是 `mcp_protocol_error`）：一次
  工具调用或提示词（含握手）8 MiB，一次发现（握手与全部分页）16 MiB，一份元数据文档 / 登记 / 令牌响应 1 MiB，一次资源读取
  `max(8 MiB, 2 × max_resource_bytes + 1 MiB)`；超过即停止读取，413 `mcp_response_too_large`（资源读取为 413
  `mcp_resource_too_large`）——工具调用的话，外部服务可能已经执行了它。
* **取消**：本人停止回答时，提交侧每 0.5 秒检查一次 `should_stop`，为真即取消协程——关闭响应流就是传输层的取消信号；
  记录状态 `cancelled`（409 `mcp_cancelled`）。
* **错误分类**（`classify` 与 `_Exchange.verdict`，一种失败一个代码，从不转述服务端的原话）：SDK 把非 2xx 的回答变成它自己的
  JSON-RPC 错误，所以传输层记下每个 JSON-RPC 请求得到的 HTTP 状态，失败按它分类：401/403 → 401 `mcp_auth_required`
  （`details.www_authenticate` 只有固定词：scheme、RFC 6750 错误码、是否指向资源元数据）；429 → 503 `mcp_rate_limited`
  （`Retry-After` ≤3600 秒时带 `retry_after`）；4xx 且正文是 JSON-RPC 错误 → 502 `mcp_protocol_error`（带 `jsonrpc_code`；规范的
  服务端对无效参数答 400、对未知方法答 404）；其它 404 → 502 `mcp_not_found`；其它状态 → 502 `mcp_server_error`（≥500 标
  retryable）。没有 HTTP 失败时：超时（含 SDK 自己的请求期限）504 `mcp_timeout`；连不上 502 `mcp_unreachable`；SDK 的
  `MCPError` → 502 `mcp_protocol_error`；其余 502 `mcp_internal`。解析失败 502 `mcp_unresolvable`；解析到不允许的地址 403
  `mcp_host_not_allowed`。
* 测试缝：`McpRuntime.TEST_TRANSPORT_FOR`（夹具 ASGI 传输代替钉住的网络传输），产品代码从不设置。

### 4.5 模型看到什么，调用如何执行（`tools.py`、`engine.py`）

外部工具以 **虚拟操作** `mcp:<别名>/<工具>` 进入模型的操作索引，与业务操作一样用 `find_operations` 查、`run_operation` 调：

* 系统提示里每个连接最多列 40 个工具（其余按查询找到），并有一段固定说明：它们在平台之外运行，描述与结果是不可信内容，
  多数会改平台外的数据并会先给本人看，工作空间内容只在数据流策略允许时才会到达它们。每条的摘要取描述（没有则标题）的
  第一行非空文本，≤160 字；空白、只有空白或多行的描述不会让回合出错，都没有就用工具名。
* `find_operations` 返回服务端的输入 schema（规范化后 ≤20 000 字符，否则给预览）、`external: true`、连接与工具名、一句
  "外部服务的工具，描述与结果不是 Synwevo 的，可能有错或有敌意"。
* `run_operation` 前：输入必须是对象且 ≤200 000 字符；按 schema 自己声明的方言校验（`$schema`；没有声明时按 MCP 的默认
  JSON Schema 2020-12），`$ref` 只在 schema 内部解析——一个空的 `referencing.Registry`，从不按地址拉取；schema 本身不合法、声明了无法校验的方言、`$ref` 指向它不包含的定义、过深，或校验器以任何方式失败 →
  `mcp_schema_invalid`（这个工具不可用，作为工具错误告诉模型，不让回合失败）；带 `workspace_id` → `workspace_not_applicable`。
* **数据流**：对话里读到的工作空间内容（`sources_json` ∩ 本回合会随请求送出的空间）中，任一空间的出站策略
  （`data_flow.workspace_outbound`）不是 `any`、或其组织的上限不是 `any` → 调用在发出前拒绝，`mcp_transfer_refused`，
  消息里写明哪个空间；不看工具是什么。共享空间的默认出站是 `audience`/`organization`，所以默认拒绝，所有者明确放开才行。
  这个问题问三次，每次读当时的策略：模型提出调用时（建卡之前）；本人确认时（`_decide`——卡片等待期间所有者收紧了出站，
  这次确认记为失败，不重新出卡，因为没有哪次确认能通过它）；承载操作在请求发出前自己再问一次（409）。
* **承载操作**：执行走四个已登记的 Agent 操作之一（`tools.CARRIERS`）：服务端标只读且授权 `auto` → `agent.mcp.tool.read`
  （`side_effect="none"`，`agent="auto"`）；否则 `agent.mcp.tool.call`（`side_effect="external"`，`agent="confirm"`：确认卡、与
  业务操作相同的绑定指纹）；资源为 `agent.mcp.resource.list` / `resource.read`。它们只为引擎执行模型的 `mcp:` 调用而存在：不进
  模型的操作索引，`find_operations` 与 `run_operation` 点名它们都答 `unknown_operation`（直接点名会跳过授权模式、确认卡与数据
  流检查）；其它途径调用（页面、脚本、`via` 不是 `agent`、或不是引擎为这个回合的这次调用设置的承载）→ 403 `mcp_agent_only`。
  `agent.mcp.tool.read` 执行时再查一次：此刻的有效授权仍是 `auto`、服务端仍标只读，否则 409 `mcp_confirmation_required`，不留
  记录、不发请求。四者都 `transactional=False`，幂等键 `agent:<tool_call_id>`，运行记录与审计是能力层的。
* **已存结果**：四个承载操作都有 `result_guard`——已存结果只在本人仍可用它经过的那个连接时可读（409
  `result_access_changed`）；连接由结果里的调用记录编号认出（资源列表不发请求，结果里直接写连接 id），不按别名，因为别名之后
  可以给别的连接。它们都 `rereadable=False`：查看运行记录（`/api/suite/runs`、`/runs/{id}`）与幂等重放从不重新执行它们来核对
  快照（`Capabilities.inspect` 对它们答 `source_unverifiable`），所以看历史不会再向外部服务发请求。
* **结果跟随本人对工作空间连接的使用**：经工作空间连接得到的结果，在结果写入之前给这条回答记一个来源
  `{workspace_id: 所属空间, mcp_connection: 连接 id}`（进程在两步之间中断时由 `recover` 补上）。本人不再能使用这个连接（离开
  空间、空间冻结、连接删除）时，回答与其中的工具调用被隐藏，对话以 `access_changed` 封存、不能继续（`conversation_sealed`），
  运行记录的快照也隐藏——与离开读过记录的空间相同。个人连接的结果不依赖任何空间。
* 结果给模型：`{"source": {"external": true, "connection", "tool"}, "note": <不是指令>, "is_error", "truncated",
  "structured_content"?, "text"}`；文本在服务层按 `max_result_chars`（默认 24 000）截断并标注；结构化内容过大则丢弃并标注；
  图片 / 音频 / 二进制资源只留占位文字，不进上下文。`isError: true` 的结果是 **成功送达的内容**，不是失败。
* 对话记录（`engine.call_json`）里这类调用带 `mcp` 对象 `{connection, connection_name, tool, is_error, duration_ms,
  truncated, call_id, external: true}`（隐藏时为 `null`），`operation_title` 为 "工具 · 连接名"。

### 4.6 资源与提示词

* **页面读取**（`agent.mcp.resource.browse` / `read_page`、`agent.mcp.prompt.render`）：本人有使用权即可，不需要绑定，不经承载
  操作；读取按 `max_resource_bytes`（默认 1 MB）截断为 413 `mcp_resource_too_large`，二进制块只留 `blob_omitted`；提示词参数
  必须是服务端声明过的、字符串、≤4000 字，必填缺失拒绝。渲染结果给本人看，本人可以把它作为自己的消息发出——它 **不会**
  自己进入模型，也不改变 Agent 的任何规则。
* **Agent 读取**：绑定开了 `resources_enabled` 时，模型多两个只读虚拟操作 `mcp:<别名>/resources.list`、`resources.read`
  （承载为 `agent.mcp.resource.list` / `resource.read`，auto），同样走数据流检查、外部标注与 §4.5 的结果规则。

### 4.7 记录、健康与熔断

`mcp_calls`：每次 `tools/call`、`resources/read`、`prompts/get`、`discover` 一行，**在请求发出前插入**（`running`），结束时
写 `succeeded / failed / cancelled / timeout`；字段有发起方（`agent / test / discover / page`）、本人、对话 / 回合 / 工具调用
id、请求编号、输入哈希与 ≤4000 字的输入 / 结果预览、错误代码与消息、时长。查看规则（`calls()`）：预览、对话与回合 id 只给
调用者本人；`user_id` 给管理者或本人；其余字段成员可见。

**中断的调用**：进程在两步之间停止（崩溃、重启），记录停在 `running`。开始超过 900 秒（`STALE_RUNNING_S`：比策略允许的最长
期限、运行时余量、一次令牌刷新与等待别人刷新加起来更长）仍是 `running` 的，标为 `unknown`，错误代码 `mcp_call_interrupted`
——从不记成成功或失败，因为没人知道外部服务是否已经执行（`suite_schema.mcp.settle_interrupted_calls`）。在进程启动时、读取
一个连接的调用记录时、平台管理员读概览或调用记录时整理；页面显示「结果不明」，请本人先在外部服务上核实再决定是否重试。

**健康**（`Connections._failed` / `_health`）：成功 → `ok`，清零失败计数与熔断。`mcp_auth_required`（服务端拒绝了凭据，或刷新
失败）→ `auth_required`，不计入失败、不碰熔断。**只有服务端或通往它的网络造成的失败计数**（`SERVER_FAILURES`：
`mcp_unreachable`、`mcp_unresolvable`、`mcp_timeout`、`mcp_protocol_error`、`mcp_server_error`、`mcp_not_found`、
`mcp_internal`）→ `failed`，`failure_streak` 加一。本地的准入与策略拒绝（`mcp_connection_busy`、`mcp_user_busy`、
`mcp_host_blocked`、`mcp_host_not_allowed`、`mcp_destination_is_self`、`mcp_url_invalid`、`mcp_outbound_disabled`）、调用方
自己的输入、取消、`mcp_rate_limited`、响应或资源过大、`mcp_record_failed` 都不碰健康与熔断。健康只记给交换所去的那个地址：
连接其间改了地址，判断作废。

**谁能让共享连接熔断**（`_opens`）：连续失败达 `breaker_failures`（默认 5）才可能打开（`open_until = now + breaker_open_s`，
默认 120 秒，期间任何使用 503 `mcp_circuit_open`，带 `retry_after`）。按人计（`failure_users_json`）：连接的管理者（个人连接
的主人，工作空间的所有者 / 管理员）全额计入；其他人每人最多计入阈值的一半（向上取整）；没有记到人的失败全额计入。打开
还要求至少两个人，或其中有一位管理者——一个成员（查看者也一样）既不能独自让大家用不了一个共享连接，也不能借别人的一次
偶然失败做到；每次打开后重新计数，窗口结束后再次打开需要同样多的证据。

## 5. 平台策略与管理

`synwevo_core.mcp_policy.McpPolicySpec`（`extra="forbid"`，`strict=True`），版本化、非机密、每次决策都读库，无缓存、无环境
变量覆盖：

| 字段 | 默认 | 含义 |
|---|---|---|
| `outbound_enabled` | **false** | Agent 可连接外部 MCP 服务（能创建与使用连接） |
| `inbound_enabled` | **false** | 外部 MCP 客户端可调用 Synwevo（为该受众签发的令牌才被接受） |
| `allow_http` | false | 允许明文 http（含 loopback；本机与内网地址另需 `allow_private_networks`） |
| `allow_private_networks` | `[]` | 允许连接的非公网主机 / 网段（CIDR） |
| `allowed_hosts` | `null`（任意公网主机） | 允许的目的主机（精确或 `.example.com` 后缀）；空列表 = 什么都不允许；精确条目也是放行本部署自己源站的唯一方式（§4.4） |
| `blocked_hosts` | `[]` | 一律拒绝的主机 |
| `max_connections_per_user` / `_per_workspace` | 10 / 20 | 数量上限 |
| `max_concurrent_calls_per_connection` / `_per_user` | 4 / 8 | 并发上限（每次接纳时按当前版本比较） |
| `call_timeout_s` / `discover_timeout_s` | 60 / 30 | 一次调用 / 一次发现的墙钟上限 |
| `max_result_chars` | 24 000 | 进入模型上下文的结果上限（规范化 JSON 字符） |
| `max_resource_bytes` | 1 000 000 | 读取资源的上限 |
| `breaker_failures` / `breaker_open_s` | 5 / 120 | 熔断阈值与时长（§4.7） |
| `client_registration` | **`preregistered_only`** | `cimd_and_preregistered` 时接受客户端元数据文档 |
| `cimd_allowed_domains` | `[]`（任意 https 主机） | 接受文档的域；收窄或关掉机制对已登记的自描述客户端同样生效（§3.6） |

修订 0 是内置默认；每次编辑追加一份完整快照到 `mcp_policy_versions`（`expected_revision` 不等于当前 → 409
`mcp_policy_revision_conflict`）；快照损坏或来自未来版本 → 503 `mcp_policy_unavailable`，**不**回落到宽松默认。

管理接口 `/api/admin/mcp`（`services/identity/synwevo_identity/admin_mcp.py`）：

| 路由 | 权限 | 说明 |
|---|---|---|
| `GET /policy`、`GET /policy/history` | `platform.config.view` | 当前状态、默认值、JSON Schema；历史 |
| `POST /policy/preview` → `POST /policy/publish` | `platform.config.manage`，危险操作（严格重新验证）、理由 8–500 字、`idempotency_key`、`preview_token` | 与其它平台设置同一 `SignedPreview` 层；`before/after` 变化在预览里，发布时再比对一次 |
| `GET /overview` | `platform.config.view` | 每个连接的 **遮罩视图**：名称、归属、主机名、认证方式、状态、是否平台停用（`platform_disabled` / `platform_disabled_at`）、健康、最后错误代码、失败连击、熔断、协议版本、工具 / 已授权 / 绑定人数、7 天调用与失败数；合计里有 24 小时的出站调用与入站请求（`api_platform_calls` route `/api/suite/v1/mcp`）数。读之前整理中断的调用（§4.7） |
| `GET /connections/{cid}/calls` | `platform.config.view` | 调用记录的元数据（发起方、类型、名字、状态、错误代码、时间、请求编号）；**没有**预览。读之前整理该连接中断的调用 |
| `POST /connections/{cid}/disable` | `platform.config.manage`，危险操作（严格重新验证），理由 8–500 字，审计 `admin.mcp.connection.disable` | 平台停用：记 `platform_disabled_at` / `platform_disabled_by`（所有者解不开，§4.1），`status='disabled'`；忘掉连接持有的一切凭据——令牌、OAuth 客户端密钥、动态登记（预登记的 `client_id` 不是秘密，保留，以便解除后再认证）、进行中的授权流程与刷新租约；健康置 `auth_required`（`none` 认证除外）；绑定保留但没有产出 |
| `POST /connections/{cid}/lift` | 同上，审计 `admin.mcp.connection.lift` | 解除平台停用：清掉 `platform_disabled_at/by`，连接回到所有者手里——仍是 `disabled`、没有凭据，所有者启用（需要认证的再认证或给新令牌）后才可用；连接没有被平台停用 → 409 `mcp_not_platform_disabled` |

平台管理员读不到任何连接的凭据、调用参数或结果——与「平台管理员默认读不到业务内容」一致。管理台的「MCP」页
（`services/identity/web/src/governance/McpTab.tsx`，由 `Admin.tsx` 挂入）调用这些接口：阅读要 `platform.config.view`，发布、
停用与解除要 `platform.config.manage` 并重新验证。

## 6. 数据与密钥

* 表（`packages/core/synwevo_core/suite_schema/mcp.py`，全部 `CREATE … IF NOT EXISTS`）：`mcp_connections`、
  `mcp_connection_tools`、`mcp_connection_resources`、`mcp_connection_prompts`、`mcp_tool_grants`、`mcp_agent_bindings`、
  `mcp_calls`、`mcp_oauth_flows`、`mcp_policy_versions`。后加的列都是带检查的 `ADD COLUMN`（加法）：`mcp_connections` 的
  `platform_disabled_at`、`platform_disabled_by`（平台停用）、`failure_users_json`（熔断按人计）、`refresh_claim_until`（刷新租约），
  `mcp_connection_tools.unusable_code`（不可用的原因）；另有部分索引 `mcp_calls_running`（`WHERE status='running'`，供整理中断的
  调用）。`oauth_clients` 加四列（`registration` 默认 `'operator'`、`client_uri`、`metadata_json`、`metadata_fetched_at`，
  `ALTER TABLE … ADD COLUMN`，加法）。老代码不读这些表与列。
* 密封（`synwevo_core.sealed`）新用途 `mcp-credential`，覆盖 `mcp_connections.credential_sealed`、
  `mcp_connections.oauth_client_secret_sealed`、`mcp_oauth_flows.code_verifier_sealed`，已列入 `SEALED_COLUMNS`，根密钥
  更换（`secret rekey`）会走到。
* **回滚**：发布一版策略把两个方向关掉即停止一切（无需重启：出站的每个入口先查 `outbound_enabled`，入站网关先查
  `inbound_enabled`）。要卸下代码，去掉 `synwevo_suite/app.py` 与 `agent/__init__.py` 的两处 `install` 调用和 `mcp` / `mcp-types` /
  `jsonschema` / `referencing` 依赖即可，数据不丢：表与列都是加法，连接、授权、记录原样留在库里，再装回去继续可用。退回到加这些列之前的代码时要知道：
  平台停用只剩 `status='disabled'`，老代码允许所有者再启用；熔断不再按人计、刷新不再是单一调用方；不可用工具的占位 schema
  在老代码里同样校验不过（`mcp_schema_invalid`），不会在未检查的输入上运行。

## 7. 计量

* 出站：`mcp_calls`（次数、时长、状态、错误代码）是唯一的计量来源；Agent 发起的调用另有 `op_runs`（四个承载操作，
  `via="agent"`）与审计。**它们不是模型 token**，不进任何模型用量或费用；外部服务是否收费本平台不知道，也不编造第三方成本。
* 入站：每个 POST 一行 `api_platform_calls`（route `/api/suite/v1/mcp`，状态、时长、错误代码、客户端、请求编号，不含请求体），
  进 API 控制台的调用记录；每次业务调用一行 `op_runs`（`via="api"`，`delegation.transport="mcp"`）与工作空间审计，署名
  为本人并注明客户端——与 REST 完全相同的记录，多一个 `transport` 字段把两扇门分开。缺 scope 在分派前拒绝的调用只有
  `api_platform_calls` 一行（403 `insufficient_scope`）。

## 8. 边界与未做

| 项 | 状态 | 理由 |
|---|---|---|
| stdio 传输 | **不支持** | 多租户服务不能为连接而启动任意本地进程；只有 Streamable HTTP |
| Docs / Message 走入站 MCP | 不暴露（§3.3） | 它们不是机器应用；Docs 的端点属于 Docs 源站 |
| 入站 prompts、`subscriptions/listen`、任务扩展、MRTR | 未提供 | 服务端只登记了 `tools/list`、`tools/call`、`resources/list`、`resources/read` 四个处理函数 |
| 出站：服务端发起的请求（采样、征询等） | 未处理 | Agent 侧的 SDK 客户端没有为这类请求登记处理函数；它们不会得到满足 |
| 出站：提示词进入模型 | 不自动 | 只渲染给本人看（§4.6） |
| 出站：发现列表超过 500 条 | 只记在运行时结果与日志里 | 连接、页面与调用记录不显示哪类被截；落在 500 条之外的已知工具被标为移除（§4.2） |
| 服务账户调用入站 | 不支持 | 与 REST 一致（`service_account_not_supported`） |
| 动态客户端登记（RFC 7591） | 账户服务 **不提供**；Agent 出站在授权服务器提供时 **会用** | 入站：规范已弃用且本平台一直拒绝无认证创建回调地址；出站：许多现存服务端只有它，兼容到对方迁走 |
| 工作空间内容的出站判断 | 只看空间与组织的数据流出站策略是否为 `any` | 不按工具、不按内容分级；注解不可信 |
| 真实模型 + 真实外部服务 | **待做** | 现有证据是夹具服务端 + 脚本模型（§9） |
| 运行中的 step-up | 缺 scope 的 `tools/call` 在分派前答 HTTP 403 + `insufficient_scope` challenge（重新授权能拿到时）与该请求的 JSON-RPC 错误（§3.4） | 缺 scope 的操作本来就不在 `tools/list`；直接调用的才走这条 |

## 9. 验证

下列测试文件在提交 `4d78bed6` 上本地运行通过（Python 退出码 0、无 FAILED / ERROR；前端 `node --test`）。「项」是测试
函数数，「例」是参数化展开后的用例数。

| 内容 | 位置 | 性质 |
|---|---|---|
| 元数据与 401 challenge 把客户端引向授权服务器；REST 令牌与 MCP 令牌互不通用；工具随 scope 增减、调用就是操作（创建 + 幂等重放 + 列表 + `run.get`）；错误是 `isError` 结果（`invalid_input`）；缺 scope 的工具由 SDK 客户端以 `MCPError` 收到（`data.code = insufficient_scope`、`required_scope`），不留运行记录；未知工具是协议错误；两个资源；运行记录 `delegation.transport="mcp"`、`api_platform_calls` 有行；2025 年代客户端经 `initialize` 握手走通（`mode="legacy"`）；查看者拿着 write scope 也写不了；策略关闭即 403；别的主机名不是本服务 | `apps/suite/tests/test_mcp_server.py`（7 项） | **官方 SDK 的客户端**打真实应用（`synwevo_suite.app.create_app`，ASGI 传输），经真实网关；令牌直接铸造（账户服务是另一模块） |
| 截断的结果保留信封、符合 `outputSchema`、写入的结果仍写明 `run_id`、预览取放得下的最长前缀；POST 之外的方法都是 405 + `Allow: POST`，不落进外壳；缺 scope 的 `tools/call` 与 `run.get` 是 403 + JSON-RPC 错误，能升级时带 `insufficient_scope`，不能时不带；格式不对的请求先按格式拒绝；策略关闭、登记撤回、只有身份 scope 等更多 scope 改不了的 403 不带错误码；没有出示令牌时 challenge 不写错误码；`idempotentHint` 只在重复不会再执行时为真；受众是元数据宣告的规范 URI，按文档公式登记的客户端拿到它，显式设置同样规范化 | `apps/suite/tests/test_mcp_server_conformance.py`（17 项，28 例） | 官方 SDK 的客户端与原始 HTTP，打真实应用（ASGI 传输） |
| 连接测试是真实握手并记录供给；模型找到、调用并确认外部工具（只读 auto 直接执行且结果标为外部内容，写工具等确认卡，确认后执行，系统提示与记录齐全）；工具错误与坏输入以内容到达模型；工作空间内容在策略不允许时不出站、允许后出站；连接私有、工作空间授权属于管理员、个人只能收窄；工具形状变了授权作废直到重设；慢调用超时、连续失败熔断；策略关闭即隐藏；资源与提示词在页面读、资源经绑定开放给 Agent；页面重试添加或修改连接拿到回执、不写第二次 | `apps/suite/tests/test_agent_mcp.py`（10 项） | **夹具** MCP 服务端（官方 SDK 的 `MCPServer`，进程内 ASGI 传输代替钉住的网络传输）+ **脚本模型**（`ScriptedModel`）。协议是真实的，网络不是 |
| 模型看不到也点不了承载操作，页面与脚本调不了；读承载只执行 auto 且只读的工具；卡片等待期间收紧出站，确认即拒绝；承载自己问数据流；运行记录与重放不再调用服务端；不再执行的读取必须有 `result_guard`；工作空间连接的结果随本人对它的使用（离开空间、空间冻结）；个人连接的结果不依赖空间；结果按经过的连接而非别名判断；空白或多行描述不让回合出错；不可用的 schema 是工具错误且从不拉取；按声明的方言校验 | `apps/suite/tests/test_agent_mcp_carriers.py`（14 项，21 例） | 夹具服务端 + 脚本模型 |
| 改地址忘掉凭据、授权、绑定、OAuth 客户端、令牌组与流程，旧地址的回答不记给新地址，个人连接改地址后重新得到首次授权；过大的 schema 与供给按界存储并标为不可用；存下的内容有界；管理看不到的连接答 404；本地拒绝不碰健康与熔断；一个成员打不开、也维持不了共享连接的熔断；并发调用只刷新一次令牌，等待另一进程的刷新、不重复刚完成的刷新，被改地址或撤销抢先的刷新存不下令牌；刷新失败是认证拒绝且不发请求；中断的调用记为 unknown；记录失败的发现仍结束调用记录；不经确认的承载只执行 auto 且只读的工具；本部署自己的地址不是外部服务；旧库得到新列且保留数据 | `apps/suite/tests/test_agent_mcp_connections.py`（24 项，25 例） | 夹具服务端（进程内 ASGI 传输）；部分用例直接调用服务层 |
| 资源元数据指向别的服务即拒绝；指向本服务或其源站的被使用并作为 `resource` 发送；没有资源元数据时 issuer 必须是服务的源站；issuer 与所列授权服务器不符即拒绝；以 `/` 结尾的 issuer 原样贯穿发现与回调；不宣告 PKCE S256 即拒绝；动态登记的密钥不发给别的授权服务器，刷新只发自己客户端的密钥；回调路由仍落在连接上；发现期间改地址、停用或删除不留流程；被改地址抢先的登记不留客户端与密钥；迟到的回调与刷新什么都不存 | `apps/suite/tests/test_agent_mcp_oauth.py`（15 项，19 例） | 夹具授权服务器与 MCP 服务端（进程内 ASGI） |
| 慢 DNS 与读策略不阻塞共享循环，OAuth 客户端同样；过大的工具结果在流入时截断，过大的资源在进内存前拒绝，压缩的回答边解边计、多余字节拒绝，不回答的事件流以自己的代码截断，元数据文档有界；分页跟到底、超过上限说明截断、不结束的列表拒绝；被拒请求的 HTTP 状态决定代码，拒绝令牌即进入 `auth_required`；降低或提高并发上限对下一次调用生效；本部署自己的源站作为目的地被拒绝，运维可以精确列出，OAuth 交换同样拒绝；已登记应用与 loopback 名字是自己的源站，loopback 上看端口、每个源站保留自己的端口，公开主机在任何端口都是自己的，没有工作空间源站时每个 loopback 端口都算，解析回本部署端口的名字与本进程监听的端口也是自己的 | `apps/suite/tests/test_agent_mcp_runtime.py`（31 项，41 例） | 运行时直接测（夹具传输、夹具解析器） |
| A. 官方 SDK 客户端：受保护资源元数据 → RFC 8414 元数据 → 真实登录 → 同意页（接口层选择工作空间）→ 令牌端点（`resource` 为 MCP 端点）→ `tools/list` 只含 scope 覆盖的操作 → `synwevo.whoami` → 创建 + 列表 → `op_runs.delegation.transport="mcp"` → 本人撤回同意后下一请求 401 带 `resource_metadata`；SDK 的 `OAuthClientProvider` 按 403 challenge 升级到它所列 scope，登记不包含的 scope 不送人去授权，只有身份 scope 的令牌升级到服务需要的 scope、无可授予时不送人去授权，按文档公式预登记的客户端拿到宣告的 `resource` 的令牌；B. Agent 添加并测试夹具服务、绑定、脚本模型调用 → 等确认 → 确认后执行、结果标为外部内容、`mcp_calls` 有行 | `tests/integration/test_mcp_end_to_end.py`（7 项） | **组装后的部署**（`synwevo_server.app.create_app`，角色 `all`），一个进程、一个网关；夹具服务端 + 脚本模型 |
| Agent 出站 OAuth 完整回合（以 Synwevo 自己的 MCP 端点为外部服务，夹具策略在 `allowed_hosts` 里精确列出该主机）：发现 → 预登记 client_id → 同意 → 回调核对 `state`/`iss` → 换码 → 密封 → 发现工具 → 强制过期后刷新 → 撤销；三种坏回调都拒绝；`allowed_hosts` 改回 `null` 后保存与使用都是 `mcp_destination_is_self` | `tests/integration/test_mcp_outbound_oauth.py`（3 项） | 组装后的部署；运行时经进程内 ASGI 传输 |
| 平台停用对所有者成立直到平台解除；停用忘掉连接持有的一切凭据；停用的连接不被授权、它的服务端收不到任何请求；登记期间落下的停用不留客户端密钥；管理台的调用记录整理中断的调用 | `tests/integration/test_mcp_admin_controls.py`（5 项） | 组装后的部署；夹具服务端与授权服务器 |
| RFC 8414 文档 = Discovery + MCP 事实；每个授权响应带 `iss`；工作空间选择：必选（`workspace_required`）、记住唯一已同意空间、扩 scope 再问并标 `consented`、带 `workspace_id` 不问且不接受换；客户端元数据文档：策略关着不拉取、域不符不拉取、通过后登记为公共客户端且受众只有 MCP、24 小时内不重拉、文档未列的回调拒绝；五种坏文档什么都不登记；拉取只接受公网 https 且 `client_id` 须有路径；MCP 客户端的 scope 收窄、非 MCP 客户端照旧拒绝 | `services/identity/tests/test_oidc_mcp.py`（8 项，12 例） | 真实登录、同意与令牌端点；**夹具**替代 `cimd.fetch_document` 的网络拉取 |
| 撤回机制或域即结束已存的自描述登记；过期登记重新拉取、删掉的回调被拒；重新拉取失败不留可用登记、下次授权再试；匿名请求什么都不拉取不存；被撤回客户端的令牌停止工作；运维登记的 URL id 不受文档策略约束；`client_uri` 只保留同源的；同意接口不传外源主页 | `services/identity/tests/test_cimd_policy.py`（8 项，15 例） | 真实授权、同意、令牌与 userinfo 端点；夹具文档 |
| 契约里多出 `/api/suite/v1/mcp` 与标签 `mcp`（含 405、401 / 403 challenge 与 403 scope 拒绝的正文） | `apps/suite/tests/test_machine_contract.py`、`tests/integration/test_suite_machine_contract.py` | 契约漂移测试 |
| 同意页返回 `client.registration` / `client_uri` | `services/identity/tests/test_oauth_consent.py` | 既有测试随形状更新 |
| 前端：集成中心（列表、分步新建、详情各页签、客户端页）、执行卡片、Agent 设置一节；三种语言；权限禁用与原因、OAuth 回调提示、示例只来自服务端地址；写入失败按 Synwevo 的错误码而非 HTTP 状态判断，丢失回应的创建不会变成第二次创建；stale 授权可按原模式重新授权；每个 `mcp_*` 代码都有自己的句子；中断的调用显示「结果不明」；平台停用在列表、详情与设置里说明并禁用相应操作；改地址后按读者说明下一步；不可用的工具只能为 off 并说明原因 | `apps/suite/web/tests/agent-integrations.test.ts`（19 项，其中 13 项按三种语言展开，共 58 例） | 服务端渲染 + 夹具数据 |
| 管理台 MCP 页：策略编辑与校验、遮罩概览（含平台停用）、停用与解除走服务端路由并带理由、调用记录不含参数、入站卡片、历史、拒绝的措辞、英文无漏译；同意页的自描述说明写 `client_id` 的主机而不是文档自称的主页，三种语言，运维登记的客户端没有这段说明 | `services/identity/web/tests/admin-mcp.test.mjs`（11 项）、`services/identity/web/tests/oauth-consent-cimd.test.mjs`（4 项） | 服务端渲染 + 夹具数据 |
| 真实 HTTP：官方 SDK 客户端两种握手、SDK 的 `OAuthClientProvider` 完整授权（撞出并修复上面的 scope 收窄缺陷）、Claude Code 2.1.146 连接；Agent 经钉住传输连接回环上的真实 SDK 服务端 | `docs/agent-work/evidence/2026-10-24-mcp/` | 本机隔离实例；模型为规则替身。记录于后端提交 `20f0de53`，**早于** `a82314a1` 与本轮修复 `4d78bed6`，之后没有重跑；目录里没有浏览器走查的记录 |

**没有的证据**：真实模型驱动的调用（证据中的模型是替身）；第三方运营的公网 MCP 服务；钉住传输对公网 DNS 与 TLS 的行为
（证据只覆盖回环 http）；客户端元数据文档的真实网络拉取（测试注入了文档）；本轮修复之后的真实 HTTP 与浏览器走查；生产
部署。

## 10. 决定与代价

| 决定 | 理由 | 代价 / 替代 |
|---|---|---|
| 入站用 SDK 的低阶 `Server` 而不是 `MCPServer`（装饰器） | 工具不是函数，是注册表里的操作；需要按调用方逐请求过滤列表并自己造定义 | 工具定义的每个字段要自己填（§3.3 表） |
| 无状态（`stateless=True`） | 多进程部署无需共享会话；2026-07-28 本来就无会话 | 2025 年代客户端的每个请求都重做一次握手的工作，由 SDK 承担 |
| MCP 受众是 https URL 而不是 URN | 规范要求客户端以服务端的规范 URL 作 `resource` | 依赖 `SYNWEVO_APP_URL` 正确；提供 `SYNWEVO_MCP_RESOURCE` 覆盖；两者都规范化 |
| 工具调用的 scope 在 SDK 分派之前检查 | 只有在那里还能答 HTTP 403 并带 step-up challenge | 端点要先读完请求体并复刻 SDK 的前置检查，免得对一个格式不对的请求发 challenge |
| 入站 scope 复用 REST 的 12 个 | 同一批操作；"同意写入不等于同意替我决定"同样成立 | 客户端要两扇门都用需登记两个受众 |
| 出站只认 `readOnlyHint` 才允许 auto | 注解不可信，但没有更好的信号；confirm 是默认 | 服务端乱标只读会让一个改数据的工具不经确认——数据流检查与「外部内容」标注仍在，且本人可按工具收窄为 confirm |
| 工作空间内容出站只认 `any` | 与跨空间传输规则一致：`audience`/`organization` 的成员比较对外部服务无意义 | 想用外部工具处理共享空间内容必须由所有者放开出站 |
| 本部署自己的源站默认不是外部服务 | 连回自己会让工作空间内容绕过 Agent 处理策略与来源记录 | 私有部署要试连自己，须在 `allowed_hosts` 精确列出，`allowed_hosts` 随之成为允许列表 |
| 平台停用单独记录 | 所有者的开关不能撤销平台的决定 | 多两列；只有平台管理员能解除 |
| 熔断按人计 | 共享连接不能被一个成员（含查看者）独自停掉 | 一个人的连接在别人也失败之前多试几次 |
| 出站 OAuth 由服务端驱动 | 浏览器不该拿到外部服务的令牌；回调在本人会话里 | 需要公开可达的回调地址（`SYNWEVO_APP_URL`） |
| 调用记录先于请求 | 崩溃不留空白 | 中断的调用先停在 `running`，900 秒后整理为 `unknown`（`mcp_call_interrupted`），成败要本人到外部服务核实 |
