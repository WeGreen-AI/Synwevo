# Synwevo 与 Model Context Protocol（MCP）

两个方向，各自独立开关，默认都关：

| 方向 | 含义 | 入口 |
|---|---|---|
| **出站**（§1） | 个人 Agent 作为 MCP 客户端，连接外部 MCP 服务，在对话里调用它的工具、读取它的资源 | `/agent/integrations`（操作 `agent.mcp.*`） |
| **入站**（§2） | Synwevo 作为 MCP 服务端，外部 MCP 客户端经账户服务授权后调用业务应用的操作 | `{SYNWEVO_APP_URL}/api/suite/v1/mcp` |

* 协议修订 2026-07-28（Streamable HTTP）；入站同时接受 2025-03-26 / 2025-06-18 / 2025-11-25 的客户端。只支持 HTTP 传输，
  **不支持 stdio**（§6）。
* 源站：工作空间源站（正式环境 `https://workplace.synwevo.com`，部署设置 `SYNWEVO_APP_URL`）。令牌由账户服务签发
  （正式环境 `https://accounts.synwevo.com`，部署设置 `SYNWEVO_ISSUER`）。
* 状态：本地实现并通过 §7 列出的测试，**尚未部署**；正式环境两个方向都要等平台管理员发布一版策略（§1.1、§2.2）才开放。
  真实模型 + 真实外部 MCP 服务的联调尚未做。
* 设计、边界与理由在 [`../platform/mcp.md`](../platform/mcp.md)；入站暴露的操作与 REST 接口
  [`suite-api.md`](suite-api.md) 是同一批。

## 1. 在 Agent 里使用外部 MCP 服务（出站）

### 1.1 前提

平台管理员把策略里的 `outbound_enabled` 发布为真（§4）。否则「集成」页只显示「本部署尚未开放」，添加连接是
403 `mcp_outbound_disabled`，Agent 看不到任何外部工具。策略还决定能连到哪些主机（`allowed_hosts` / `blocked_hosts`、
是否允许内网与明文 http）、每人 / 每空间的连接数、并发与超时。

### 1.2 添加连接

页面 `/agent/integrations` → 「添加」（`/agent/integrations/new`），三步：

1. **归属与地址。** 连接属于**个人**（只有本人能管、能用）或某个**工作空间**（所有者 / 管理员管理，成员可用；只列出本人管理的
   空间）。地址必须是 http(s) 绝对地址，不含片段与账号口令，≤2048 字符；明文 http（本机地址也一样）需策略 `allow_http`，本机或内网地址还需在 `allow_private_networks` 中列出；主机过策略
   （`mcp_host_blocked` / `mcp_host_not_allowed`）。名称 ≤80 字，说明 ≤500 字。
2. **认证方式。** `none`；`bearer`（粘贴一个访问令牌，服务端密封保存，页面只再显示末四位）；`oauth`（§1.3，可填对方预先
   登记给你的 `client_id` 与可选 `client_secret`，密封保存）。
3. **创建并测试。** 创建是 `agent.mcp.connection.create`；非 OAuth 连接随即自动测试（`agent.mcp.connection.test`）：一次真实
   的协议握手，读取服务端的 `serverInfo`、能力与 `instructions`，再分页读取工具、资源、提示词（每类最多 500 条）并记录
   下来。测试成功后连接健康为 `ok`，页面显示服务名、协议版本与三类数量。

数量上限：每人默认 10、每空间默认 20（409 `mcp_connection_limit`）。改地址等于换服务：已发现的工具、资源、提示词标为移除，
健康与熔断清零，原有授权随工具形状哈希自然失效（§1.5）。

### 1.3 OAuth 认证

认证由 **Synwevo 服务端**驾驭，浏览器不接触外部服务的令牌：

1. 在连接上点「认证」（`agent.mcp.oauth.start`，须为连接的管理者，连接须为 `oauth` 类型）。服务端向该地址发一条
   `server/discover`，期望 401；从 `WWW-Authenticate` 取 `resource_metadata` 与 `scope`，读取对方的受保护资源元数据
   （RFC 9728）与授权服务器元数据（RFC 8414 / OpenID Connect Discovery），校验 `issuer`，要求 PKCE S256。
2. 客户端身份按规范优先级：你填写的预登记 `client_id` → 曾对同一 issuer 动态登记过的 id → **Synwevo Agent 的客户端元数据文档**
   `{SYNWEVO_APP_URL}/.well-known/mcp-client/synwevo-agent.json`（对方宣告 `client_id_metadata_document_supported` 且
   `SYNWEVO_APP_URL` 为 https 时）→ 对方提供 `registration_endpoint` 时的动态登记（公共客户端）。都没有：
   409 `mcp_oauth_registration_unavailable`，请先到对方登记并填写 `client_id`。
3. 页面把浏览器送到返回的 `authorization_url`（PKCE、`state`、`resource`、发现得到的 `scope`）。流程 600 秒内有效。
4. 对方回调到 `{SYNWEVO_APP_URL}/api/suite/agent/mcp/oauth/callback`（必须在本人已登录的会话里）：核对 `state`、`iss`
   （RFC 9207）后换码、密封保存令牌组、顺手做一次发现，然后 303 回 `/agent/integrations/{connection_id}?auth=ok`；失败则是
   `?auth=error&code=<错误码>`（`mcp_oauth_denied`、`mcp_oauth_issuer_mismatch`、`mcp_oauth_flow_expired`、
   `mcp_oauth_token_failed` 等）。
5. 访问令牌到期前 30 秒内有刷新令牌则自动刷新；刷新失败连接健康置为 `auth_required`，调用答 `mcp_auth_required`，需要再认证。
   「撤销凭据」（`agent.mcp.credential.revoke`）忘掉令牌但保留连接。

对方的授权服务器若要事先登记 Synwevo Agent，用上述元数据文档的地址作 `client_id`；它登记的回调只有上面那一个，
`token_endpoint_auth_method` 为 `none`。正式环境：`https://workplace.synwevo.com/.well-known/mcp-client/synwevo-agent.json`，
回调 `https://workplace.synwevo.com/api/suite/agent/mcp/oauth/callback`。

### 1.4 发现与刷新

「测试」（`agent.mcp.connection.test`，管理者）与「刷新能力」（`agent.mcp.connection.refresh`，使用者即可）是同一次真实握手。
每个工具存名字、标题、描述（≤4000 字）、输入 / 输出 schema、注解和一个**形状哈希**（名字 + 输入 schema + 输出 schema +
`readOnlyHint` + `destructiveHint`）。服务端不再列出的工具标为移除，再出现时恢复。

### 1.5 授权工具（连接级，管理者设置）

每个工具一条授权（`agent.mcp.grant.set`）：

| 模式 | 含义 |
|---|---|
| `off` | Agent 不得使用 |
| `confirm` | 可用，每次调用先给本人看确认卡 |
| `auto` | 不经确认执行；**只允许服务端标 `readOnlyHint: true` 的工具**，否则 409 `mcp_auto_requires_read_only` |

授权钉在设置时的形状哈希上：服务端改了工具的输入 / 输出 schema 或只读提示，授权变为 **stale**，按 `off` 对待，直到有人
再看一眼并重新设置。个人连接第一次发现时每个工具自动得到 `confirm`；此后新出现的工具不自动获得授权；工作空间连接在
管理员决定之前没有任何授权。注解是服务端自报的提示，不可信：`auto` 的工具仍受 §1.7 的数据流检查与「外部内容」标注。

### 1.6 让自己的 Agent 使用（个人绑定）

工作空间加了连接不会让任何人的 Agent 多出工具。每个人自己决定（`agent.mcp.binding.set`）：

* 启用 / 停用这个连接；
* **别名**（slug，≤24 字符，本人范围内唯一，409 `mcp_alias_taken`）——模型用它称呼连接：`mcp:<别名>/<工具>`；
* 是否把**资源**开放给 Agent（默认不开）；
* 按工具的个人模式：只能 `off` 或 `confirm`，即只能比连接级授权更严，不能更宽。

Agent 实际可用的工具 = 策略开放 ∧ 绑定启用 ∧ 连接 active 且本人仍可用 ∧ 授权不是 off 且不 stale ∧ 个人模式再收窄
（`agent.mcp.binding.list` 返回这个集合）。

### 1.7 Agent 如何使用

* 外部工具以虚拟操作 `mcp:<别名>/<工具>` 进入模型的操作索引：系统提示里每个连接最多列 40 个，其余由模型用
  `find_operations` 按查询找到；每条都标 `external: true`，并注明描述与结果来自 Synwevo 之外、可能有错或有敌意。
* 调用前输入按服务端公布的 JSON Schema（Draft 2020-12）校验；schema 本身不合法或过深 → `mcp_schema_invalid`，工具不可用。
* **确认卡**：授权为 `confirm`（或服务端未标只读）的工具每次调用先停在确认卡上，本人看到连接名、工具名与完整参数后确认
  才执行；`auto` 且只读的工具直接执行。确认机制与业务操作相同（同一绑定指纹）。
* **数据流**：对话里读到的工作空间内容会随请求送出时，每个这样的空间的数据流出站策略（及其组织的上限）都必须是 `any`，
  否则在发出前拒绝（`mcp_transfer_refused`，消息里写明是哪个空间），不看工具是什么。共享空间的默认出站不是 `any`，所以
  默认拒绝；所有者要在空间设置里明确放开。规则见 [`../platform/data-flow.md`](../platform/data-flow.md)。
* **结果**：进入模型的是 `{"source": {"external": true, "connection", "tool"}, "note": <不是指令>, "is_error", "truncated",
  "structured_content"?, "text"}`；文本按策略 `max_result_chars`（默认 24 000 字符）截断并标注；图片、音频、二进制只留占位
  文字。服务端返回 `isError: true` 是**送达的内容**（模型据此调整），不是调用失败。
* 对话记录里这类调用带 `mcp` 对象 `{connection, connection_name, tool, is_error, duration_ms, truncated, call_id,
  external: true}`，操作标题显示为「工具 · 连接名」。
* 本人停止回答即取消进行中的调用（记录状态 `cancelled`）。每连接并发默认 4、每人默认 8，满了直接拒绝
  （429 `mcp_connection_busy` / `mcp_user_busy`，`retry_after`）而不排队；单次调用超时默认 60 秒（`mcp_timeout`）。
* 连续失败达阈值（默认 5 次）熔断默认 120 秒：期间任何使用 503 `mcp_circuit_open`（`retry_after`）。

### 1.8 资源与提示词

* 页面上有使用权即可浏览与读取资源（`agent.mcp.resource.browse` / `read_page`），不需要绑定；读取上限
  `max_resource_bytes`（默认 1 MB，超出 413 `mcp_resource_too_large`），二进制块只报 `blob_omitted`。
* 绑定里开了「资源开放给 Agent」后，模型多两个只读操作 `mcp:<别名>/resources.list`、`mcp:<别名>/resources.read`
  （不经确认，同样过数据流检查与外部标注）。
* 提示词只在页面渲染给本人看（`agent.mcp.prompt.render`，参数须是服务端声明过的字符串，≤4000 字）；本人可以把渲染结果当
  自己的消息发出。它**不会**自动进入模型，也不改变 Agent 的任何规则。

### 1.9 记录

每次调用（工具、资源、提示词、发现）在请求发出**之前**就在 `mcp_calls` 里有一行，结束时写状态
（`succeeded / failed / cancelled / timeout`）、时长、错误码与 ≤4000 字的输入 / 结果预览。「调用记录」（`agent.mcp.calls`）：
预览、对话与回合 id 只给调用者本人；`user_id` 给管理者或本人；其余字段成员可见。连接的增删改、授权、绑定、认证都写审计
（`suite.agent.mcp.*`），审计里只有主机名、认证方式与改了哪些字段，没有地址路径，没有凭据。

### 1.10 错误码

| 状态 | `code` | 含义 |
|---|---|---|
| 400 | `mcp_url_invalid` | 地址不是 http(s) 绝对地址、含片段 / 凭据，或 http 未被策略放开 |
| 403 | `mcp_outbound_disabled`、`mcp_manage_forbidden`、`mcp_host_blocked`、`mcp_host_not_allowed` | 策略未开放；不是连接的管理者；主机被禁 / 不在允许列表（含解析到内网地址） |
| 404 | `mcp_connection_not_found`、`mcp_tool_not_found`、`mcp_tool_unavailable`、`mcp_prompt_not_found`、`mcp_resources_unavailable` | 看不到 = 不存在；工具已移除；工具未授权 / 已 stale / 连接不可用；资源未开放给 Agent |
| 409 | `mcp_connection_limit`、`mcp_connection_disabled`、`mcp_auth_required`、`mcp_auto_requires_read_only`、`mcp_alias_taken`、`mcp_auth_kind_mismatch`、`mcp_auth_not_required`、`mcp_cancelled` | 数量上限；连接已停用；需要先认证；auto 只给只读工具；别名已用；非 OAuth 连接点了认证；对方不要求认证；本人取消 |
| 401 | `mcp_auth_required` | 对方拒绝了当前凭据（HTTP 401 / 403）或刷新失败 |
| 413 | `mcp_resource_too_large` | 资源超过上限 |
| 429 | `mcp_connection_busy`、`mcp_user_busy` | 并发已满（`retry_after`） |
| 502 | `mcp_unreachable`、`mcp_unresolvable`、`mcp_not_found`、`mcp_server_error`、`mcp_protocol_error`、`mcp_internal`、`mcp_oauth_discovery_failed`、`mcp_oauth_unsupported`、`mcp_oauth_registration_failed`、`mcp_oauth_token_failed` | 连不上；解析失败；对方 404；对方其它 HTTP 错误；JSON-RPC 错误（`jsonrpc_code`）；其它；OAuth 发现 / 能力 / 登记 / 换码失败 |
| 503 | `mcp_circuit_open`、`mcp_rate_limited`、`mcp_oauth_unavailable` | 熔断中；对方 429；部署没有 `SYNWEVO_APP_URL`，无法回调 |
| 504 | `mcp_timeout` | 对方未在限时内响应 |

错误消息是平台自己的固定文案，从不转述外部服务的原话。

## 2. 用外部 MCP 客户端连接 Synwevo（入站）

### 2.1 地址

一切地址都从两个部署设置推出；页面「连接你的 MCP 客户端」（`agent.mcp.inbound.info`）显示的就是当前部署的真实值。

| 项 | 模板 | 正式环境 |
|---|---|---|
| MCP 端点（也是令牌的 `resource` / 受众） | `{SYNWEVO_APP_URL}/api/suite/v1/mcp` | `https://workplace.synwevo.com/api/suite/v1/mcp` |
| 受保护资源元数据（RFC 9728） | `{SYNWEVO_APP_URL}/.well-known/oauth-protected-resource/api/suite/v1/mcp` | `https://workplace.synwevo.com/.well-known/oauth-protected-resource/api/suite/v1/mcp` |
| 授权服务器元数据（RFC 8414；与 OpenID Connect Discovery 同一份） | `{SYNWEVO_ISSUER}/.well-known/oauth-authorization-server` | `https://accounts.synwevo.com/.well-known/oauth-authorization-server` |
| 授权 / 令牌端点 | `{SYNWEVO_ISSUER}/oidc/authorize`、`{SYNWEVO_ISSUER}/oidc/token` | `https://accounts.synwevo.com/oidc/…` |

只登记 `POST`；传输是 Streamable HTTP，一个 POST 一条 JSON-RPC 消息，响应是 JSON 或仅限本次请求的 SSE 流（按客户端
`Accept`），通知答 202；请求体上限 4 MiB。没有会话、没有握手（2025 年代客户端的 `initialize` 也以无状态方式回答），
部署的任何进程都能回答任何请求。只回答 `Host` 为 `SYNWEVO_APP_URL` 主机的请求（SDK 的 DNS 重绑定保护）。

### 2.2 管理员：开放入站并决定客户端如何登记

1. 在 MCP 策略里把 `inbound_enabled` 发布为真（§4）。关着时任何请求都是 403 `mcp_inbound_disabled`。
2. 客户端身份，二选一或都用：

**(a) 预登记**（默认 `client_registration = preregistered_only`）。在账户服务容器里：

```bash
python -m synwevo_identity.cli client register <client_id> --app platform --name "<显示名>" \
  --redirect <客户端的回调地址> \
  --scopes "openid offline_access forms.read tables.read tables.write" \
  --audience {SYNWEVO_APP_URL}/api/suite/v1/mcp
```

* `--redirect` 可重复，精确匹配，无通配符；非本机地址必须 https。
* `--scopes`：照例带 `openid`（未指定 scope 的授权请求默认就是它）；要刷新令牌加 `offline_access`；业务 scope 见
  [`suite-api.md` §1.2](suite-api.md)（与 REST 同一批 12 个）。同意页按 scope 逐项列出。
* `--audience` **必须写 MCP 端点**。省略时带业务 scope 的 `platform` 客户端默认受众是 REST 的 `urn:synwevo:platform:suite`，
  拿到的令牌打不开 MCP（`invalid_audience`）。一个客户端两扇门都要用就写两个 `--audience`，申请令牌时用 `resource` 分别指定
  （一枚令牌只能有一个受众）。
* 不加 `--confidential` 即公共客户端：没有密钥，PKCE S256 必需。正式环境把 `{SYNWEVO_APP_URL}` 换成
  `https://workplace.synwevo.com`。

**(b) 客户端元数据文档**（Client ID Metadata Documents）。把策略的 `client_registration` 发布为 `cimd_and_preregistered`，
可用 `cimd_allowed_domains` 限定接受哪些域的文档（精确主机或 `.example.com` 后缀；空 = 任意 https 主机）。之后客户端以它
自己托管的 https 文档地址作 `client_id`，无需运维登记：账户服务第一次见到该 id 时拉取文档（https、公网地址、不跟随重定向、
64 KiB、10 秒、`Content-Type: application/json`），校验后登记为 `platform` 应用的**公共**客户端（`registration='cimd'`），
受众只有 MCP 端点，24 小时内复用登记，之后重新拉取。文档必须满足：

```json
{
  "client_id": "https://<你的主机>/<路径>.json",
  "client_name": "<显示名，≤120 字>",
  "client_uri": "https://<你的主机>",
  "redirect_uris": ["https://<你的主机>/oauth/callback"],
  "grant_types": ["authorization_code", "refresh_token"],
  "response_types": ["code"],
  "token_endpoint_auth_method": "none"
}
```

`client_id` 必须与文档地址完全相等；`redirect_uris` 1–10 条（非本机必须 https）；`token_endpoint_auth_method` 只能是 `none`
（本平台不给没见过的客户端发密钥）；`grant_types` 须含 `authorization_code`。违反任一条是 400 `invalid_client`，什么都不登记。
运维对同一 id 手工登记过的，以手工登记为准。元数据里 `client_id_metadata_document_supported` 随策略为 `true` / `false`。

账户服务**不提供**动态客户端登记（RFC 7591）端点。

### 2.3 客户端的授权流程

1. 向端点 POST（例如 `server/discover` 或 `tools/list`）不带令牌 → 401，`WWW-Authenticate: Bearer error="invalid_token",
   error_description="unauthorized", resource_metadata="<元数据地址>"`。
2. 读元数据：`resource`（端点自身）、`authorization_servers = [{SYNWEVO_ISSUER}]`、`scopes_supported`（10 个业务 scope：五个应用各自的 read 与 write；三个 `*.decide` 不在其中，客户端需在授权请求里明确写出）、
   `bearer_methods_supported = ["header"]`。
3. 读 `{SYNWEVO_ISSUER}/.well-known/oauth-authorization-server`：`code_challenge_methods_supported = ["S256"]`、
   `authorization_response_iss_parameter_supported = true`、`client_id_metadata_document_supported`。
4. 授权码 + PKCE S256，`resource` = 端点地址，`scope` 为所需业务 scope（按 MCP 规范，没有指定时客户端取上面的
   `scopes_supported`）。客户端登记了 MCP 受众时，超出其登记范围的 scope 被**收窄**掉（RFC 6749 §3.3），令牌响应的
   `scope` 写明实际授予；一个登记范围内的业务 scope 都不剩才是 `invalid_scope`。可选 `workspace_id=<空间 id>` 直接指定
   工作空间（§2.4）。
5. 本人登录、同意（§2.4）；授权响应带 `code`、`state`、`iss={SYNWEVO_ISSUER}`——客户端必须校验 `iss`。
6. 令牌端点换码，带 `resource`（省略时用客户端登记的第一个受众）。请求了未登记的 `resource` 或一次请求两个受众 →
   `invalid_target`。刷新令牌只能用一次：每次刷新返回后继者，再次出示已用过的会撤销整组授权。
7. 之后每次请求带 `Authorization: Bearer <access_token>`。被拒绝时 401 / 403 都带 RFC 6750 challenge：`insufficient_scope`
   时 `scope="<所需 scope>"`，客户端可以据此申请更大权限（step-up）。

### 2.4 同意页与工作空间选择

一枚令牌只属于一个工作空间。`platform` 应用的客户端申请了需要同意的 scope 而 `authorize` 没带 `workspace_id` 时，同意接口
（`GET /api/auth/oauth-consent/{rid}`）返回 `workspace_choices: [{id, name, role, consented}]`，本人选一个，批准
（`POST …/approve`，JSON `{"workspace_id": …}`）；给了选择而没选是 400 `workspace_required`。例外：本人此前对该客户端只在一个
在用空间同意过且覆盖所申请的 scope → 直接用它，不再问；只有一个可用空间 → 直接用。带了 `workspace_id` 的请求不给选择，
也不接受换成别的。同意接口还返回 `client.registration`（`operator` / `cimd`）与 `client.client_uri`，让本人看清是谁在申请。

同意页（`/oauth/consent`）在有选择时列出这些工作空间（单选，标出已授权过的），没有选择时「批准」不可用；自描述登记的客户端
会标明它的 `client_uri` 主机，并说明 Synwevo 没有核实它的运营方。

权限是三者的交集，每次调用重新计算：客户端登记的 scope ∩ 本人同意的 scope ∩ **本人此刻**在该空间的角色与应用资格。
查看者拿着 `forms.write` 也写不了。撤权规则与 REST 相同（本人在「已授权的应用」撤回、令牌撤销、客户端停用、被移出空间……
下一次请求即 401 / 403）。

### 2.5 工具、资源与调用语义

* **工具 = 操作。** `tools/list` 列出本人在该空间可用且令牌 scope 覆盖的业务操作（名字就是操作 id，如 `forms.form.list`；
  输入 schema 是操作自己的，加可选 `idempotency_key` 与 `correlation_id`；输出 schema 是信封
  `{run_id, operation, status, output, replayed, workspace_id, truncated}`），加两个平台工具 `synwevo.whoami`、
  `synwevo.run.get`。顺序确定，列表 `ttlMs = 60000`、`cacheScope = "private"`。注解按操作声明填：`readOnlyHint` = 无副作用，
  `destructiveHint` = 有副作用且 id 以 `.delete/.archive/.cancel/.withdraw/.revoke/.close` 结尾，`idempotentHint` = 只读或接受
  幂等键，`openWorldHint` = 触达平台外的人。`_meta` 里有 `com.synwevo/scope`、`app`、`side_effect`、`mode`、`version`。
* **先调 `synwevo.whoami`**：本人、客户端、工作空间、角色、scope、可用应用、到期时间。
* **调用 = `Capabilities.invoke(via="api")`**：校验、本人实时角色、运行记录、幂等、事件与 REST 完全一致。有副作用的操作可带
  `idempotency_key`（1–128 个可见 ASCII 字符）；会向他人发出请求、通知或链接的操作**必须**带（缺少是 `idempotency_key_required`）；
  同键同参重放第一次的结果（`replayed: true`），同键不同参 `idempotency_conflict`。键按客户端命名空间存储，与 REST 同一规则。
* **异步操作**不是完成：`status` 为 `queued` / `running` 时用 `synwevo.run.get` 轮询 `run_id`（只回答本客户端为本人在本空间
  发起的运行）。
* **结果**：`structuredContent` 是信封，`content` 的文本块是同一份 JSON；超过 400 000 字符时返回前缀并 `truncated: true`。
  `_meta["com.synwevo/request_id"]` 是请求编号。
* **错误**：业务层的拒绝是 `isError: true` 的结果，正文是 REST 的错误契约 `{"error": {code, message, request_id, details}}`
  （`insufficient_scope` 带 `details.required_scope`，`invalid_input` 带 `details.fields`）；未知工具名是 JSON-RPC `-32602`。
* **资源**：`synwevo://whoami`（同上）与 `synwevo://operations`（本人在该空间可用的全部操作契约 + 每条 `granted`：令牌是否持有
  它需要的 scope），`application/json`。**没有提示词**（§6）。
* 每个 POST 一行 `api_platform_calls`（route `/api/suite/v1/mcp`），进 API 控制台的调用记录；每次业务调用一行 `op_runs`
  （`via="api"`，`delegation.transport="mcp"`）与工作空间审计，署名为本人并注明客户端。速率限制与 REST 共用
  （按凭据、按工作空间，429 `rate_limited`，`Retry-After`）。

### 2.6 配置示例（模板）

MCP 客户端通常只要端点地址，其余靠发现：

```json
{ "url": "{SYNWEVO_APP_URL}/api/suite/v1/mcp" }
```

正式环境即 `{"url": "https://workplace.synwevo.com/api/suite/v1/mcp"}`。客户端若要你手填 OAuth 参数：授权服务器
`{SYNWEVO_ISSUER}`，`client_id` 为运维登记的 id（或你托管的元数据文档地址），`resource` 为端点地址，scope 按 §2.2。

探测（不带令牌，期望 401 与 challenge）：

```bash
curl -sS -i {SYNWEVO_APP_URL}/.well-known/oauth-protected-resource/api/suite/v1/mcp
curl -sS -i -X POST {SYNWEVO_APP_URL}/api/suite/v1/mcp \
  -H "Accept: application/json, text/event-stream" -H "Content-Type: application/json" \
  -H "MCP-Protocol-Version: 2026-07-28" -H "Mcp-Method: tools/list" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

官方 Python SDK（`mcp` 2.3.0）持令牌调用——与 `apps/suite/tests/test_mcp_server.py` 用的同一条路：

```python
import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

ENDPOINT = "{SYNWEVO_APP_URL}/api/suite/v1/mcp"          # 正式环境 https://workplace.synwevo.com/api/suite/v1/mcp
async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {access_token}"}) as http:
    async with Client(streamable_http_client(ENDPOINT, http_client=http)) as client:
        who = (await client.call_tool("synwevo.whoami", {})).structured_content
        made = (await client.call_tool("forms.form.create",
                                       {"title": "Travel request", "idempotency_key": "trip-1"})).structured_content
```

### 2.7 不在入站里的

* **Synwevo Docs（`docweave.*`）与 Synwevo Message（`messages.*`）**：它们的适配器不是机器应用，REST 操作接口同样没有它们。
  这是边界：Docs 的 MCP 端点如果要做，属于 Docs 源站（`docwave.synwevo.com`）与 Docs 自己的凭据规则，不借用工作空间源站的受众。
* **个人 Agent 的一切**：能力层对 `via="api"` 一律 `person_required`。
* **服务账户**（`client_credentials`）：与 REST 一致，`service_account_not_supported`。
* **REST 的令牌**：受众不同，`invalid_audience`；反之亦然。
* **跨工作空间写入**：凭据只属于一个空间。

## 3. 环境变量

| 变量 | 必需 | 作用 |
|---|---|---|
| `SYNWEVO_APP_URL` | 已有 | 工作空间源站。决定 MCP 端点、受众、元数据地址、Agent 的回调与客户端元数据文档地址；没有它出站 OAuth 答 503 `mcp_oauth_unavailable`，入站受众是 `urn:synwevo:platform:mcp:unconfigured`（没有客户端会用它申请令牌，受众检查不可能误通过） |
| `SYNWEVO_ISSUER` | 已有 | 账户服务；受保护资源元数据里的 `authorization_servers` |
| `SYNWEVO_MCP_RESOURCE` | **可选** | 覆盖入站的规范 URI / 令牌受众（默认 `{SYNWEVO_APP_URL}/api/suite/v1/mcp`）。只在端点对外地址与 `SYNWEVO_APP_URL` 不一致时才需要；元数据地址随之推出 |

没有新增必需变量。策略不读环境变量：开关与限额只来自已发布的策略版本。

## 4. 管理与计量

管理接口 `/api/admin/mcp`（账户服务，`services/identity/synwevo_identity/admin_mcp.py`）：

| 路由 | 权限 | 说明 |
|---|---|---|
| `GET /policy`、`GET /policy/history` | `platform.config.view` | 当前策略（修订 0 = 内置默认）、默认值、JSON Schema；历史 |
| `POST /policy/preview` → `POST /policy/publish` | `platform.config.manage`，危险操作（严格重新验证）、理由 8–500 字、`idempotency_key`、`preview_token` | 与其它平台设置同一签名预览层；`expected_revision` 不等于当前 → 409 `mcp_policy_revision_conflict` |
| `GET /overview` | `platform.config.view` | 每个连接的遮罩视图（名称、归属、主机名、认证方式、状态、健康、最后错误码、失败连击、熔断、协议版本、工具 / 已授权 / 绑定人数、7 天调用与失败数）；合计含 24 小时出站调用数与入站请求数 |
| `GET /connections/{cid}/calls` | `platform.config.view` | 调用记录元数据（发起方、类型、名字、状态、错误码、时间、请求编号），**没有**预览 |
| `POST /connections/{cid}/disable` | `platform.config.manage`，危险操作，理由 | 平台级停用：状态 `disabled`、凭据忘掉、绑定保留但产出为空 |

策略字段与默认值见设计记录 §5。平台管理员读不到任何连接的凭据、调用参数或结果。管理台的「MCP」页
（`services/identity/web/src/governance/McpTab.tsx`）调用这些接口；写作时该页尚未挂入管理台导航（设计记录 §8）。

**计量。** 出站以 `mcp_calls`（次数、时长、状态、错误码）为唯一来源，Agent 发起的另有 `op_runs`（`agent.mcp.tool.read` /
`tool.call`）与审计；入站每个 POST 一行 `api_platform_calls`，每次业务调用一行 `op_runs`。**它们不是模型 token**，不进任何
模型用量或费用；外部服务是否收费本平台不知道，也不编造第三方成本。

## 5. 回滚

* **功能级**：发布一版策略把 `outbound_enabled` / `inbound_enabled` 关掉即停止一切，无需重启——出站每个入口先查
  `outbound_enabled`，入站网关先查 `inbound_enabled`。已有连接、授权、绑定、记录原样留在库里。
* **代码级**：表与列都是加法（`CREATE … IF NOT EXISTS`、`ALTER TABLE … ADD COLUMN`），老代码不读它们。去掉
  `synwevo_suite/app.py` 与 `agent/__init__.py` 里的两处 `install` 调用和 `mcp` / `mcp-types` / `jsonschema` 依赖即可卸下，
  数据不丢；再装回去继续可用。
* 密封列（`mcp_connections.credential_sealed`、`oauth_client_secret_sealed`、`mcp_oauth_flows.code_verifier_sealed`，用途
  `mcp-credential`）已列入 `SEALED_COLUMNS`，根密钥更换（`secret rekey`）会一并迁移。

## 6. 已知限制

| 项 | 状态 |
|---|---|
| stdio 传输 | **不支持**：多租户服务不能为连接而启动任意本地进程；只有 Streamable HTTP |
| 入站 prompts、`subscriptions/listen`、任务扩展、MRTR | 未提供；服务端只有 `tools/list`、`tools/call`、`resources/list`、`resources/read` |
| 入站动态客户端登记（RFC 7591） | 不提供；用预登记或客户端元数据文档 |
| 出站：服务端发起的请求（采样、征询） | 不处理，不会得到满足 |
| 出站：提示词进入模型 | 不自动，只渲染给本人看 |
| 工作空间内容出站的判断 | 只看空间与组织的数据流出站策略是否为 `any`，不按工具、不按内容分级 |
| 运行中的 step-up | 令牌缺少 scope 的操作不出现在 `tools/list`；直接调用它得到 `isError` 结果 `insufficient_scope`（带 `required_scope`），不是 HTTP 403 challenge——客户端需要更大权限时重新授权 |
| 真实模型 + 真实外部 MCP 服务 | **未做**；现有证据见 §7 |

## 7. 验证

| 内容 | 位置 | 性质 |
|---|---|---|
| 元数据与 401 challenge 把客户端引向授权服务器；REST 令牌与 MCP 令牌互不通用；工具随 scope 增减、调用就是操作（创建 + 幂等重放 + 列表 + `run.get`）；错误是 `isError` 结果；未知工具是协议错误；两个资源；运行记录 `delegation.transport="mcp"`；2025 年代客户端经 `initialize` 握手走通；查看者拿着 write scope 也写不了；策略关闭即 403；别的主机名不是本服务 | `apps/suite/tests/test_mcp_server.py`（7 项） | 官方 SDK 客户端打真实应用（ASGI 传输），经真实网关；令牌直接铸造 |
| 连接测试是真实握手；模型找到、调用并确认外部工具（只读 auto 直接执行且结果标为外部内容，写工具等确认卡）；工具错误与坏输入以内容到达模型；工作空间内容在策略不允许时不出站；归属与授权规则；工具形状变了授权作废；超时与熔断；策略关闭即隐藏；资源与提示词 | `apps/suite/tests/test_agent_mcp.py`（9 项） | **夹具** MCP 服务端（官方 SDK 的 `MCPServer`，进程内 ASGI 传输）+ **脚本模型**。协议是真实的，网络不是 |
| 真实登录 → 同意（含工作空间选择）→ 令牌端点（`resource` 为 MCP 端点）→ MCP 列表与调用 → 撤回同意即 401；Agent 连接夹具服务、脚本模型调用、确认后执行、记录齐全 | `tests/integration/test_mcp_end_to_end.py`（2 项） | 组装后的部署（`synwevo_server.app.create_app`，角色 `all`）；夹具服务端 + 脚本模型 |
| RFC 8414 元数据 = Discovery + MCP 事实；每个授权响应带 `iss`；工作空间选择（必选、记住、扩 scope 再问、带 `workspace_id` 不问）；客户端元数据文档只在策略允许的域下登记公共客户端、24 小时内不重拉、未列回调拒绝；五种坏文档什么都不登记；文档只从公网 https 拉取 | `services/identity/tests/test_oidc_mcp.py`（6 项，其一参数化） | 真实登录、同意与令牌端点；**夹具**替代文档的网络拉取 |
| 契约里多出 `/api/suite/v1/mcp`（标签 `mcp`） | `apps/suite/tests/test_machine_contract.py`、`tests/integration/test_suite_machine_contract.py` | 契约漂移测试 |
| 同意接口返回 `client.registration` / `client_uri` | `services/identity/tests/test_oauth_consent.py` | 既有测试随形状更新 |
| MCP 客户端申请全部宣告的 scope 时收窄到登记范围、令牌响应写明；非 MCP 客户端照旧拒绝未登记 scope | `services/identity/tests/test_oidc_mcp.py`（新增 2 项） | 真实登录、同意与令牌端点 |
| Agent 出站 OAuth 完整回合：以 Synwevo 自己的 MCP 端点为外部服务，发现 → 预登记 client_id → 同意（选工作空间）→ 回调核对 `state`/`iss` → 换码 → 令牌组密封 → 用它发现工具 → 强制过期后刷新 → 撤销凭据；回调状态不符、`iss` 不符、重用 state 都拒绝 | `tests/integration/test_mcp_outbound_oauth.py`（2 项） | 组装后的部署；运行时经进程内 ASGI 传输到达本部署 |
| 集成中心、执行卡片、Agent 设置一节、管理台 MCP 页、同意页工作空间选择在三种语言下的渲染与交互规则 | `apps/suite/web/tests/agent-integrations.test.ts`（25 项）、`services/identity/web/tests/admin-mcp.test.mjs`（10 项） | 服务端渲染 + 夹具数据 |
| 真实 HTTP：官方 SDK 客户端（2026-07-28 与 2025-11-25 两种握手）、SDK 自己的 `OAuthClientProvider` 完整授权、Claude Code 2.1.146 连接；Agent 经钉住传输连接真实外部 MCP 服务（官方 SDK 服务端，回环）；浏览器走通新建连接、授权工具、绑定、对话执行与确认、客户端页、管理台发布策略（含重新验证与审计）、同意页选择 | `docs/agent-work/evidence/2026-10-24-mcp/` | 本机隔离实例（`SYNWEVO_TEST_INSTANCE=1`），模型为规则替身 |

**没有的证据**：真实模型驱动的工具调用（证据里的模型是测试替身）；第三方运营的公网 MCP 服务；钉住传输对公网 DNS 与 TLS 的
行为（证据只覆盖回环 http）；客户端元数据文档的真实网络拉取；生产部署。
