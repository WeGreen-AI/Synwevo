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
* 状态：本地实现，§7 列出的测试在提交 `4d78bed6` 上通过；部署状态不在这里记录（见
  [`../quality/deployment.md`](../quality/deployment.md)）。任何部署的两个方向都要等平台管理员发布一版策略（§1.1、§2.2）
  才开放。真实模型 + 真实外部 MCP 服务的联调尚未做。
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
   空间）。工作空间连接与空间里的原生操作过同一道门：空间所属的组织被冻结或删除时，它的连接对谁都不可用（不列出、页面 404、
   Agent 看不到、已有结果不可读），解冻后恢复；为这样的空间添加连接是 404 `workspace_not_found`。地址必须是 http(s) 绝对地址，不含片段与账号口令，≤2048 字符；明文 http（本机地址也一样）需策略 `allow_http`，本机或内网地址还需在 `allow_private_networks` 中列出；主机过策略
   （`mcp_host_blocked` / `mcp_host_not_allowed`）。名称 ≤80 字，说明 ≤500 字。
   地址**不能是本部署自己的**（403 `mcp_destination_is_self`）：工作空间、账户服务、门户、站点等本部署源站与已登记应用的
   地址，在任何端口上都算；本机地址（`localhost`、`127.0.0.1`、`[::1]`、`*.localhost`）只在本部署所用的端口上算，别的端口是别的
   本机服务；一个解析回本机本部署端口的名字也算。把 Agent 连回本部署，工作空间的内容会经工具结果回到模型，绕过 Agent 对
   原生操作的处理策略与来源记录。确有需要（例如私有部署试连自己）时，平台管理员在策略的 `allowed_hosts` 里精确写出该主机
   （`.example.com` 这样的后缀规则不算）——`allowed_hosts` 由此成为允许列表，其它目的地也都要列出才能连。
2. **认证方式。** `none`；`bearer`（粘贴一个访问令牌，服务端密封保存，页面只再显示末四位）；`oauth`（§1.3，可填对方预先
   登记给你的 `client_id` 与可选 `client_secret`，密封保存）。
3. **创建并测试。** 创建是 `agent.mcp.connection.create`；非 OAuth 连接随即自动测试（`agent.mcp.connection.test`）：一次真实
   的协议握手，读取服务端的 `serverInfo`、能力与 `instructions`，再分页读取工具、资源、提示词（§1.4）并记录下来。测试成功后
   连接健康为 `ok`，页面显示服务名、协议版本与三类数量。创建或修改的回应丢失时，按同一幂等键重试拿到的是当时写入的回执，
   不会建出第二个连接。

数量上限：每人默认 10、每空间默认 20（409 `mcp_connection_limit`）。

**改地址等于换服务**，旧服务得到的一切都不带过去：已发现的工具、资源、提示词标为移除；工具授权删除；**所有人**的 Agent
绑定被关掉（别名与个人收窄保留）；bearer 令牌、OAuth 令牌组、OAuth 客户端（包括预登记的 `client_id` 与密钥）忘掉，进行中的
认证作废；健康与熔断清零，连接显示需要认证（`none` 认证除外）——除非同一次修改就给了新服务自己的凭据。之后：

* 管理者：给新服务的令牌，或重新认证（§1.3）→ 测试 → 授权工具（个人连接测试后每个可用工具都是「确认后执行」，核对即可；
  工作空间连接的工具在管理员授权之前不交给任何 Agent）；
* 每个使用者：在「我的 Agent」里重新启用这个连接（需要时重新允许读取资源）。

改地址时还在进行的测试，结果不记录（409 `mcp_address_changed`）。

**凭据写给它所对应的地址。** 用 `agent.mcp.connection.update` 给 `credential` 或 `oauth_client` 而不同时给 `url` 时，必须带
`expected_url`——你所看到的、这个凭据所对应的服务地址（页面自动带上它显示的地址；API、CLI 与脚本要自己带，CLI / SDK 没有专门的
凭据命令，走通用的操作调用）——否则 422 `invalid_input`（`field: expected_url`；连接被平台停用或地址被停用时先得到那两个拒绝）。
连接此刻已经不是这个地址（另一位管理者改了它）→ 409 `mcp_address_changed`，凭据不保存，不会交给新地址；核对新地址后再给。
只改名称、说明或改为不认证不需要它。

**平台停用。** 平台管理员可以停用任何连接（§4），这与所有者的开关分开：停用会忘掉连接保存的凭据；在平台解除之前，所有者
不能启用、测试、认证、调用、读资源、渲染提示词，也不能改地址、认证方式或凭据（409 `mcp_platform_disabled`），Agent 看不到
它。所有者仍可查看、改名称与说明、设授权、改自己的绑定、关闭、撤销凭据，但不能删除它（409 `mcp_platform_disabled`）：它是
这次停用的记录。**停用也关闭它的服务地址**——同一主机、端口与路径，不论大小写、路径参数（`;v=1`）、点段、末尾 `/` 或查询
怎样书写：解除之前，任何人都不能为这个地址添加连接或改指向它、给凭据、启用、测试、认证或调用（403 `mcp_destination_stopped`，
`detail.host`），已经指向它的其它连接同样停止工作，平台管理员自己的 Agent 也一样；从这个地址改指别处可以。同一主机的其它路径
不受影响（要停整台主机，用策略的 `blocked_hosts`）。解除后连接仍是停用状态、没有凭据：所有者启用并（需要时）重新认证即可。

### 1.3 OAuth 认证

认证由 **Synwevo 服务端**驾驭，浏览器不接触外部服务的令牌：

1. 在连接上点「认证」（`agent.mcp.oauth.start`，须为连接的管理者，连接须为 `oauth` 类型；平台停用的连接在发出任何请求之前就
   拒绝）。服务端向该地址发一条 `server/discover`，期望 401；从 `WWW-Authenticate` 取 `resource_metadata` 与 `scope`，读取对方的
   受保护资源元数据（RFC 9728）与授权服务器元数据（RFC 8414 / OpenID Connect Discovery），并检查：
   * 资源元数据必须指向这个服务（与连接地址同源，连接的路径在它所指路径之下），否则 502 `mcp_oauth_discovery_failed`
     （`reason: resource_mismatch`）；授权请求里的 `resource` 用资源元数据里的值；没有资源元数据的老服务以自身源站为授权
     服务器、以连接地址为 `resource`；
   * 授权服务器元数据的 `issuer` 必须与它的发现地址所用的 issuer **逐字相同**（以 `/` 结尾的 issuer 原样保留），否则 502
     `mcp_oauth_discovery_failed`（`reason: issuer_mismatch`）；
   * 元数据必须在 `code_challenge_methods_supported` 里列出 `S256`——没有这个字段同样拒绝，502 `mcp_oauth_unsupported`。
   * issuer 按发布的原样比较与记录（主机大小写、默认端口都不改写），回调的 `iss` 与它逐字比较。
2. 客户端身份按规范优先级：你填写的预登记 `client_id` → 曾对同一 issuer 动态登记过的 id → **Synwevo Agent 的客户端元数据文档**
   `{SYNWEVO_APP_URL}/.well-known/mcp-client/synwevo-agent.json`（对方宣告 `client_id_metadata_document_supported`、**并宣告
   `authorization_response_iss_parameter_supported`**，且 `SYNWEVO_APP_URL` 为 https 时）→ 对方在其 issuer 源站上提供
   `registration_endpoint` 时的动态登记（公共客户端；对方接受元数据文档却不回传 `iss`、又有登记端点时也用它）。都没有：409
   `mcp_oauth_registration_unavailable`，请先到对方登记并填写 `client_id`（`reason: iss_unsupported`：对方接受元数据文档但不回传
   `iss`；`endpoint_origin_mismatch`：登记端点不在 issuer 源站上）。客户端密钥只发给它所属的授权服务器：你预登记的客户端只发给它
   绑定的那台（下面），动态登记得到的只发给登记它的 issuer；这次用的是元数据文档时，旧密钥不发送。

   为什么元数据文档要求 `iss`：它的 `client_id` 是所有接受文档的授权服务器都接受的同一个公共 id。一个恶意的 MCP 服务可以让它
   自己的授权服务器把浏览器转给一个诚实的授权服务器，诚实服务器的授权码随后回到 Synwevo 的回调、被送到恶意的令牌端点
   （混淆攻击，RFC 9700 §4.4.1）；只有 RFC 9207 的 `iss` 能分辨回调来自哪台服务器，所以对方必须宣告会回传它，回调没有它或不符即
   拒绝。这个 id 不论怎样出现都一样——对方登记端点把它当作自己签发的 id 交回、或有人把它填作预登记 `client_id`；换一种写法（大小写、
   写出默认端口、加查询）也还是它：本部署源站上的任何 URL 都按它对待。对方不回传
   `iss` 时：授权或令牌端点不在 issuer 源站上 → 502 `mcp_oauth_discovery_failed`（`reason: endpoint_origin_mismatch`，`endpoints`
   列出它们），否则 409 `mcp_oauth_registration_unavailable`（`reason: iss_unsupported`）。端点源站的检查只针对这个共用 id。

   **你预登记的客户端属于你登记它的那台授权服务器**（MCP 授权规范的 "Authorization Server Binding"）：第一次认证完成时，连接
   记下那台授权服务器的 issuer（之前保存的连接按最近一次完成的认证）；MCP 服务列出多台授权服务器时，用列表里你的那一台；之后 MCP 服务不再列出它 → 409
   `mcp_oauth_issuer_changed`（`detail.issuer`、`detail.registered_issuer`），认证没有开始，`client_id` 与密钥都不交出去；确属预期
   时，在新的授权服务器登记客户端并给连接新的 `client_id`。给新的客户端即重新绑定（改地址或改认证方式连客户端一起忘掉）；
   平台停用保留它。端点在
   哪个源站都可以用（例如 Amazon Cognito、Google 这类把登录域放在 issuer 旁边的服务器）。**动态登记**属于签发它的那台服务器，
   同样不看端点的源站。仍未挡住的一种：攻击者先以 Synwevo 的名义在一台开放动态登记、不回传 `iss` 的诚实服务器上登记，再由恶意
   服务的登记端点交回这个 id（§6）。
3. 页面把浏览器送到返回的 `authorization_url`（PKCE、`state`、`resource`、发现得到的 `scope`）。流程 600 秒内有效。
4. 对方回调到 `{SYNWEVO_APP_URL}/api/suite/agent/mcp/oauth/callback`（必须在本人已登录的会话里）：核对 `state`、`iss`
   （RFC 9207，逐字比较）后换码、密封保存令牌组、顺手做一次发现，然后 303 回 `/agent/integrations/{connection_id}?auth=ok`；
   失败则是 `?auth=error&code=<错误码>`（`mcp_oauth_denied`、`mcp_oauth_issuer_mismatch`、`mcp_oauth_flow_expired`、
   `mcp_oauth_token_failed`、`mcp_connection_changed` 等）。用的是 Synwevo 的元数据文档时，回调必须带 `iss`，不论这次认证开始时
   记下了什么。开始认证的人必须到最后仍能决定它：仍管理这个连接、连接与它的地址未被平台停用；回调时或换码期间（在保存令牌的
   同一个事务里再查一次）被移出空间、降级或停用，这次认证以 `refused` 结束，什么都不保存（`mcp_connection_not_found`、
   `mcp_manage_forbidden`、`mcp_platform_disabled`、`mcp_destination_stopped`）；你预登记的客户端已属于另一台授权服务器时同样
   如此（`mcp_oauth_issuer_changed`），不换码。认证期间连接被改地址、改认证方式、平台停用或删除时，这次认证什么都
   不保存（409 `mcp_connection_changed`，或回调时 404 `mcp_oauth_flow_not_found`），请核对连接后重新认证。
5. 访问令牌到期前 30 秒内有刷新令牌则自动刷新，一个连接同一时刻只有一个调用方刷新，其余等它的结果（刷新令牌只用一次，
   对方启用轮换时不会被重复使用而撤销整组授权）；等满 120 秒仍未完成答 429 `mcp_connection_busy`。令牌端点拒绝了刷新
   （401，或 400 且 `invalid_grant` / `invalid_client` / `unauthorized_client`）时连接健康置为 `auth_required`，调用答
   `mcp_auth_required`，需要再认证；授权服务器连不上、超时、出错或答非所问时，你的授权仍然有效，调用答它自己的代码
   （`mcp_unreachable`、`mcp_timeout`、`mcp_server_error`、`mcp_rate_limited`、`mcp_protocol_error`，响应过大是 413
   `mcp_response_too_large`……，`detail.cause` 为 `token_refresh`），稍后重试即可——服务端失败那几种计入健康与熔断，集成页写明是
   授权服务器在刷新令牌时出的问题（对话里的工具卡片目前仍按代码说明，§6）。被一次新的认证或
   改动抢先的刷新什么都不保存（`mcp_connection_changed`）。「撤销凭据」（`agent.mcp.credential.revoke`）忘掉令牌但保留连接。
6. **追加权限（step-up）**：服务端以 403 `insufficient_scope` 拒绝一次调用时，它点名的 scope（只接受 RFC 6750 的 scope 写法，最多
   64 个、2048 字符）记到连接上，下一次认证一并申请，最多追加 5 次；拒绝的 `detail.step_up` 是 `requested`（下次认证会申请）、
   `already_requested`（上次认证已经申请过，再认证多半没用——请对方为你的账户开通）或 `capped`（已到上限）。改地址或改认证方式
   后重新计数。

对方的授权服务器若要事先登记 Synwevo Agent，用上述元数据文档的地址作 `client_id`；它登记的回调只有上面那一个，
`token_endpoint_auth_method` 为 `none`。这个 id 是共用的，所以即使是这样登记的，对方也必须宣告并回传 `iss`（见上）。正式环境：`https://workplace.synwevo.com/.well-known/mcp-client/synwevo-agent.json`，
回调 `https://workplace.synwevo.com/api/suite/agent/mcp/oauth/callback`。

### 1.4 发现与刷新

「测试」（`agent.mcp.connection.test`，管理者）与「刷新能力」（`agent.mcp.connection.refresh`，使用者即可）是同一次真实握手。
每个工具存名字、标题、描述（≤4000 字）、输入 / 输出 schema、注解和一个**形状哈希**（名字 + 输入 schema + 输出 schema +
`readOnlyHint` + `destructiveHint`）。服务端不再列出的工具标为移除，再出现时恢复。

* **列表**：每类最多 500 条。更长的列表只保留前 500 条——这一点目前只写进服务端日志，页面不显示，此前记录过、这次落在 500
  条之外的工具会被标为移除。分页不结束（同一个 cursor 再次出现，或连续 20 页为空）→ 502 `mcp_protocol_error`。
* **大小**：一次发现收到的字节上限 16 MiB；每个工具的输入或输出 schema 存为 JSON 后 ≤20 000 字符，标题 ≤200 字；一个连接的
  工具、资源、提示词合计 ≤512 KiB。schema 不是 JSON 对象、过大，或合计已放不下的工具记为**不可用**（页面写明原因与上限），
  只能设为 `off`，不交给任何 Agent。
* 改了地址的连接，发现结果只记给新地址（§1.2）。

### 1.5 授权工具（连接级，管理者设置）

每个工具一条授权（`agent.mcp.grant.set`）：

| 模式 | 含义 |
|---|---|
| `off` | Agent 不得使用 |
| `confirm` | 可用，每次调用先给本人看确认卡 |
| `auto` | 不经确认执行；**只允许服务端标 `readOnlyHint: true` 的工具**，否则 409 `mcp_auto_requires_read_only` |

不可用的工具只能是 `off`（其它模式 409 `mcp_tool_unusable`）。授权钉在设置时的形状哈希上：服务端改了工具的输入 / 输出 schema
或只读提示，授权变为 **stale**，按 `off` 对待，直到有人再看一眼并重新设置（工具页签提供「按原模式重新授权」）。个人连接第一次
发现时每个可用工具自动得到 `confirm`（改地址后的第一次发现同样如此）；此后新出现的工具不自动获得授权；工作空间连接在
管理员决定之前没有任何授权。注解是服务端自报的提示，不可信：`auto` 的工具仍受 §1.7 的数据流检查与「外部内容」标注。

### 1.6 让自己的 Agent 使用（个人绑定）

工作空间加了连接不会让任何人的 Agent 多出工具。每个人自己决定（`agent.mcp.binding.set`）：

* 启用 / 停用这个连接；
* **别名**（slug，≤24 字符，本人范围内唯一，409 `mcp_alias_taken`）——模型用它称呼连接：`mcp:<别名>/<工具>`；
* 是否把**资源**开放给 Agent（默认不开）；
* 按工具的个人模式：只能 `off` 或 `confirm`，即只能比连接级授权更严，不能更宽。

Agent 实际可用的工具 = 策略开放 ∧ 绑定启用 ∧ 连接 active、未被平台停用且本人仍可用 ∧ 工具可用 ∧ 授权不是 off 且不 stale ∧
个人模式再收窄（`agent.mcp.binding.list` 返回这个集合）。

### 1.7 Agent 如何使用

* 外部工具以虚拟操作 `mcp:<别名>/<工具>` 进入模型的操作索引：系统提示里每个连接最多列 40 个，其余由模型用
  `find_operations` 按查询找到；每条都标 `external: true`，并注明描述与结果来自 Synwevo 之外、可能有错或有敌意。描述为空、
  只有空白或有多行时取第一行非空文本，都没有就用工具名。
* 模型只能经这些 `mcp:` 操作调用外部工具。背后执行它们的四个已登记操作（`agent.mcp.tool.read`、`agent.mcp.tool.call`、
  `agent.mcp.resource.list`、`agent.mcp.resource.read`）不出现在模型的索引里，模型点名它们得到 `unknown_operation`；页面或脚本
  直接调用是 403 `mcp_agent_only`。
* 调用前输入按服务端公布的 JSON Schema 校验，用它在 `$schema` 里声明的方言（没有声明时为 Draft 2020-12）；`$ref` 只在 schema
  内部解析，从不按地址拉取。schema 本身不合法、方言无法校验、`$ref` 指向不存在的定义、或过深 → `mcp_schema_invalid`，工具
  不可用——这是给模型的工具错误，不会让整轮回答失败。
* **确认卡**：授权为 `confirm`（或服务端未标只读）的工具每次调用先停在确认卡上，本人看到连接名、工具名与完整参数后确认
  才执行；`auto` 且只读的工具直接执行——执行时再查一次，授权已改为确认或服务端不再标只读则答 409
  `mcp_confirmation_required`，不发请求。确认机制与业务操作相同（同一绑定指纹）。
* **数据流**：对话里读到的工作空间内容会随请求送出时，每个这样的空间的数据流出站策略（及其组织的上限）都必须是 `any`，
  否则在发出前拒绝（`mcp_transfer_refused`，消息里写明是哪个空间，并说明空间的内容包括它共享的 MCP 连接返回的结果、这些结果
  只能交回同一个连接），不看工具是什么。**工作空间连接的结果就是那个空间的内容**：只有该空间的模型处理政策允许当前模型时，模型
  才读得到它，否则模型读到 `{withheld: true, code: "mcp_result_withheld", workspace_id, reason}`——是哪个空间、经它的共享连接、由
  它的 Agent 处理政策决定（连接已认不出时 `workspace_id` 为 `null`，同样不给）；你在对话里仍看得到结果，共享空间默认不允许。
  被扣下的结果不算送出过，不影响之后的调用。读到之后，这次对话再调用别的外部服务或写入别的空间，都要该空间的数据流策略允许。
  唯一的例外是回到它来的地方：同一个连接、同一地址的结果再经这个连接调用（先搜索、再取回）不算内容离开平台；同一空间的内容
  另以别的方式读到过，或连接其间改了地址，照样拒绝。共享空间的默认出站不是 `any`，所以默认拒绝；所有者要在空间设置里明确放开。这在模型提出调用时、本人确认时、请求发出时各查一次：确认卡等待期间所有者收紧了
  出站，确认即记为失败，不会发出。规则见 [`../platform/data-flow.md`](../platform/data-flow.md)。
* **结果**：进入模型的是 `{"source": {"external": true, "connection", "tool"}, "note": <不是指令>, "is_error", "truncated",
  "structured_content"?, "text"}`；文本按策略 `max_result_chars`（默认 24 000 字符）截断并标注；图片、音频、二进制只留占位
  文字。服务端返回 `isError: true` 是**送达的内容**（模型据此调整），不是调用失败。一次工具调用收到的字节上限 8 MiB，超出即
  停止读取（413 `mcp_response_too_large`；外部服务可能已经执行了它）。结果在记录之前裁到运行记录放得下（约 960 KiB：先丢结构化
  内容，再截文本，标 `truncated`）；万一调用执行了、结果却没能记下，这次调用显示为**结果不明**（`outcome_unresolved`），模型被告知
  不要重做，这轮回答就此结束——请先在外部服务上核实。
* **结果跟着连接的使用权走**：经工作空间连接得到的结果，只在本人仍能使用该连接时可见。本人离开空间、空间被冻结或连接被删除
  后，这条回答与其中的工具调用被隐藏，对话封存（`access_changed`），不能再继续。个人连接的结果不依赖任何空间。查看运行记录
  不会再向外部服务发请求：已存的结果按它经过的连接核对，不重新执行。
* 对话记录里这类调用带 `mcp` 对象 `{connection, connection_name, tool, is_error, duration_ms, truncated, call_id,
  external: true, withheld}`，操作标题显示为「工具 · 连接名」。`withheld` 在模型没有读这条结果时是 `{code: "mcp_result_withheld",
  workspace_id, workspace_name}`，否则 `null`；对话页的工具卡片目前还不显示它（§6）。
* 本人停止回答即取消进行中的调用（记录状态 `cancelled`）。每连接并发默认 4、每人默认 8，满了直接拒绝
  （429 `mcp_connection_busy` / `mcp_user_busy`，`retry_after`）而不排队；改了策略对下一次调用生效。单次调用超时默认 60 秒
  （`mcp_timeout`）。
* **熔断**：只有服务端或网络造成的失败（连不上、解析失败、超时、协议错误、服务端错误、对方 404、其它内部失败）计数；本地的
  并发与策略拒绝、凭据被拒（连接改为需要认证）、对方限流、响应过大、取消都不计。连续失败达阈值（默认 5 次）熔断默认 120 秒：
  期间任何使用 503 `mcp_circuit_open`（`retry_after`）。共享连接上不是管理者的人每人最多计入阈值的一半（向上取整），打开还要求
  至少两个人或其中有一位管理者——一个成员（含查看者）不能独自让大家都用不了。

### 1.8 资源与提示词

* 页面上有使用权即可浏览与读取资源（`agent.mcp.resource.browse` / `read_page`），不需要绑定；读取上限
  `max_resource_bytes`（默认 1 MB，超出 413 `mcp_resource_too_large`；收到的字节边收边计，远超上限的响应不会整个读进内存），
  二进制块只报 `blob_omitted`。读到的资源与渲染的提示词在记录之前同样裁到运行记录放得下（资源先只留各内容的地址与类型、再
  去掉它们、再截文本；提示词先丢消息、再丢描述、再截文本），标 `truncated`。
* 绑定里开了「资源开放给 Agent」后，模型多两个只读操作 `mcp:<别名>/resources.list`、`mcp:<别名>/resources.read`
  （不经确认，同样过数据流检查与外部标注，结果同样跟着连接的使用权走）。资源列表（与 `agent.mcp.resource.browse` 的结果
  `{items, truncated}`）是发现时存下的内容，过长时先去掉标题与描述、再只留放得下的前若干条，标 `truncated`；模型读到其中不超过
  `max_result_chars` 的部分。列表不向外部服务发请求，记录不下时是模型听得到的普通失败，从不显示为「结果不明」。
* 提示词只在页面渲染给本人看（`agent.mcp.prompt.render`，参数须是服务端声明过的字符串，≤4000 字）；本人可以把渲染结果当
  自己的消息发出。它**不会**自动进入模型，也不改变 Agent 的任何规则。

### 1.9 记录

每次调用（工具、资源、提示词、发现）在请求发出**之前**就在 `mcp_calls` 里有一行，结束时写状态
（`succeeded / failed / cancelled / timeout`）、时长、错误码与 ≤4000 字的输入 / 结果预览。进程在调用进行中停止（崩溃、重启）
留下的记录，开始超过 15 分钟后（在进程启动或读取调用记录时）标为 `unknown`、错误码 `mcp_call_interrupted`，页面显示「结果不明」——请先在外部服务上核实是否已
执行，再决定是否重试。「调用记录」（`agent.mcp.calls`）：预览、对话与回合 id 只给调用者本人；`user_id` 给管理者或本人；其余
字段成员可见。连接的增删改、授权、绑定、认证都写审计（`suite.agent.mcp.*`），审计里只有主机名、认证方式与改了哪些字段，没有
地址路径，没有凭据。

### 1.10 错误码

| 状态 | `code` | 含义 |
|---|---|---|
| 400 | `mcp_url_invalid`、`mcp_oauth_issuer_mismatch`、`mcp_oauth_denied`、`mcp_oauth_state_missing`、`mcp_oauth_code_missing` | 地址不是 http(s) 绝对地址、含片段 / 凭据，或 http 未被策略放开；回调的 `iss` 不符或缺失（对方宣告会回传，或用的是元数据文档）；对方拒绝授权；回调缺 `state` / 授权码 |
| 401 | `mcp_auth_required` | 对方拒绝了当前凭据（HTTP 401 / 403；缺 scope 时带 `detail.step_up`），或令牌端点拒绝了刷新 |
| 403 | `mcp_outbound_disabled`、`mcp_manage_forbidden`、`mcp_host_blocked`、`mcp_host_not_allowed`、`mcp_destination_is_self`、`mcp_destination_stopped`、`mcp_agent_only` | 策略未开放；不是连接的管理者；主机被禁 / 不在允许列表（含解析到内网地址）；地址是本部署自己的；地址被另一个连接的平台停用关闭（`detail.host`）；承载操作被直接调用 |
| 404 | `workspace_not_found`、`mcp_connection_not_found`、`mcp_tool_not_found`、`mcp_tool_unavailable`、`mcp_prompt_not_found`、`mcp_resources_unavailable`、`mcp_oauth_flow_not_found` | 为不可用（含组织冻结）的空间添加连接；看不到 = 不存在（管理操作也一样）；工具已移除；工具未授权 / 已 stale / 不可用 / 连接不可用；资源未开放给 Agent；认证流程不存在、不属于你或已作废 |
| 409 | `mcp_connection_limit`、`mcp_connection_disabled`、`mcp_platform_disabled`、`mcp_auth_required`、`mcp_auto_requires_read_only`、`mcp_tool_unusable`、`mcp_alias_taken`、`mcp_auth_kind_mismatch`、`mcp_auth_not_required`、`mcp_oauth_registration_unavailable`、`mcp_oauth_issuer_changed`、`mcp_oauth_flow_decided`、`mcp_connection_changed`、`mcp_address_changed`、`mcp_confirmation_required`、`mcp_transfer_refused`、`mcp_cancelled` | 数量上限；所有者停用了连接；平台停用了连接（也不能删除）；需要先认证；auto 只给只读工具；工具不可用；别名已用；非 OAuth 连接点了认证；对方不要求认证；对方不接受可用的登记方式（`reason: iss_unsupported` / `endpoint_origin_mismatch`）；你预登记的客户端属于另一台授权服务器，MCP 服务却指向了新的（`detail.issuer` / `registered_issuer`）；认证已处理过；认证或请求期间连接被改动（调用没有发出、认证没有保存）；凭据所对应的地址、或测试期间的地址已被改掉；工具现在需要确认；工作空间内容不允许出站；本人取消 |
| 410 | `mcp_oauth_flow_expired` | 认证流程超过 600 秒 |
| 413 | `mcp_resource_too_large`、`mcp_response_too_large` | 资源超过上限；外部服务的响应超过上限（工具调用可能已执行；带 `detail.cause: token_refresh` 时是授权服务器的令牌响应） |
| 422 | `invalid_input`（`field: expected_url`） | 给了凭据或 OAuth 客户端，却没说它对应哪个地址（§1.2） |
| 429 | `mcp_connection_busy`、`mcp_user_busy`、`mcp_resolver_busy` | 并发已满，或令牌正由另一个调用方刷新（`retry_after`）；本服务同时进行的地址解析已满，请求没有发出（`retry_after`） |
| 500 | `mcp_record_failed` | 外部服务已应答，但这次发现的结果没能保存（调用记录里的代码） |
| 502 | `mcp_unreachable`、`mcp_unresolvable`、`mcp_not_found`、`mcp_server_error`、`mcp_protocol_error`、`mcp_internal`、`mcp_oauth_discovery_failed`、`mcp_oauth_unsupported`、`mcp_oauth_registration_failed`、`mcp_oauth_token_failed` | 连不上；解析失败；对方 404；对方其它 HTTP 错误；JSON-RPC 错误（`jsonrpc_code`）或分页不结束；其它（`mcp_unreachable`、`mcp_unresolvable`、`mcp_server_error`、`mcp_protocol_error`——以及 504 `mcp_timeout`、503 `mcp_rate_limited`——带 `detail.cause: token_refresh` 时发生在授权服务器、刷新令牌时，授权仍有效）；OAuth 发现失败（含 `resource_mismatch`、`issuer_mismatch`、`endpoint_origin_mismatch`）/ 不支持 PKCE S256 / 登记 / 换码失败 |
| 503 | `mcp_circuit_open`、`mcp_rate_limited`、`mcp_oauth_unavailable` | 熔断中；对方 429；部署没有 `SYNWEVO_APP_URL`，无法回调 |
| 504 | `mcp_timeout` | 对方未在限时内响应 |
| — | `mcp_call_interrupted` | 调用记录状态 `unknown`：调用进行中服务停止，结果未知（§1.9） |
| — | `mcp_result_withheld` | 不是请求的失败：模型没有读这条工作空间连接的结果（空间的处理政策不允许当前模型，或连接已认不出），模型的输入与对话记录的 `mcp.withheld` 带着它（§1.7） |

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

端点与受众按规范形式写出：scheme 与主机小写、没有默认端口、没有片段、路径末尾没有 `/`——即使部署设置写成
`https://Workplace.Synwevo.com:443`，元数据的 `resource`、令牌受众与客户端送回的值也是同一个字符串。客户端在令牌请求里用另一种
写法指向这个端点（主机大写、`:443`、末尾 `/`）同样可以，拿到的令牌受众是规范形式。配置成 `…/api/suite/v1/mcp/`（末尾带 `/`）
的客户端在任何令牌被读取之前被 308 到不带 `/` 的端点。`SYNWEVO_ISSUER` 必须写成规范形式（小写 scheme 与主机、无默认端口、无
查询与片段）：否则每个角色拒绝启动——客户端会把 issuer 解析成规范形式再与 `iss` 逐字比较，非规范的 issuer 永远比不上。

传输是 Streamable HTTP，一个 POST 一条 JSON-RPC 消息，通知答 202；请求体上限 4 MiB。请求的 `Accept` 必须**同时**含
`application/json` 与 `text/event-stream`（只写其一是 406）；回答 JSON 还是 SSE 由服务端决定：2026-07-28 的请求在 15 秒内处理完
且没有通知时回 JSON，否则回仅限本次请求的 SSE（每 15 秒一行 keepalive）；2025 年代（`initialize` 握手）的请求一律回 SSE。
POST 之外的方法（GET、DELETE 等）带有效令牌时答 405，`Allow: POST`——没有 GET 流，也没有会话可供 DELETE 结束；不带令牌
先是 401。没有会话、没有握手
（2025 年代客户端的 `initialize` 也以无状态方式回答），部署的任何进程都能回答任何请求。只回答 `Host` 为工作空间源站或 MCP
端点主机的请求（SDK 的 DNS 重绑定保护）。

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
  拿到的令牌打不开 MCP（`invalid_audience`）。写法不必是规范形式：指向本 MCP 端点的受众保存时换成规范形式。一个客户端两扇门
  都要用就写两个 `--audience`，申请令牌时用 `resource` 分别指定（一枚令牌只能有一个受众）。
* 不加 `--confidential` 即公共客户端：没有密钥，PKCE S256 必需。正式环境把 `{SYNWEVO_APP_URL}` 换成
  `https://workplace.synwevo.com`。

**(b) 客户端元数据文档**（Client ID Metadata Documents）。把策略的 `client_registration` 发布为 `cimd_and_preregistered`，
可用 `cimd_allowed_domains` 限定接受哪些域的文档（精确主机或 `.example.com` 后缀；空 = 任意 https 主机）。之后客户端以它
自己托管的 https 文档地址作 `client_id`，无需运维登记：账户服务在需要时拉取文档（https、公网地址、不跟随重定向、64 KiB、
10 秒、`Content-Type: application/json`），校验后登记为 `platform` 应用的**公共**客户端（`registration='cimd'`），受众只有 MCP
端点。

* **每次授权都重新检查**：策略此刻是否仍接受这个机制与这个域；登记超过 24 小时就重新拉取，按文档此刻的内容更新（文档删掉的
  回调从此不被接受；重新拉取失败则这次授权被拒，下次再试）。
* **只为已登录的人拉取**：需要拉取而请求者还没有登录时，账户服务什么都不拉取、不保存，先送去登录，登录后回到同一个授权请求。
* **撤回即失效**：关掉机制或把域移出允许范围后，已登记的自描述客户端在下一次使用时（而不是到期时）停止工作——令牌端点答
  `invalid_client`，MCP 端点答 401 `client_unavailable`（`reason: cimd_not_allowed`）；登记与令牌原样留着，重新允许即恢复。

文档必须满足：

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
`client_uri` 只在与 `client_id` 同源（同主机、同端口、https）时保留，否则丢弃。运维对同一 id 手工登记过的，以手工登记为准，
不受上述策略约束。元数据里 `client_id_metadata_document_supported` 随策略为 `true` / `false`。

账户服务**不提供**动态客户端登记（RFC 7591）端点。

### 2.3 客户端的授权流程

1. 向端点 POST（例如 `server/discover` 或 `tools/list`）不带令牌 → 401，`WWW-Authenticate: Bearer
   resource_metadata="<元数据地址>"`（没有出示令牌，所以不写错误码）。
2. 读元数据：`resource`（端点自身）、`authorization_servers = [{SYNWEVO_ISSUER}]`、`scopes_supported`（10 个业务 scope：五个应用
   各自的 read 与 write；`extract.decide`、`approvals.decide` 不在其中，客户端需在授权请求里明确写出）、
   `bearer_methods_supported = ["header"]`。
3. 读 `{SYNWEVO_ISSUER}/.well-known/oauth-authorization-server`：`code_challenge_methods_supported = ["S256"]`、
   `authorization_response_iss_parameter_supported = true`、`client_id_metadata_document_supported`。
4. 授权码 + PKCE S256，`resource` = 端点地址，`scope` 为所需业务 scope（按 MCP 规范，没有指定时客户端取上面的
   `scopes_supported`）。客户端登记了 MCP 受众时，超出其登记范围的 scope 被**收窄**掉（RFC 6749 §3.3），令牌响应的
   `scope` 写明实际授予；一个登记范围内的业务 scope 都不剩才是 `invalid_scope`。可选 `workspace_id=<空间 id>` 直接指定
   工作空间（§2.4）。
5. 本人登录、同意（§2.4）；授权响应带 `code`、`state`、`iss={SYNWEVO_ISSUER}`——客户端必须校验 `iss`。
6. 令牌端点换码，带 `resource`（省略时用客户端登记的第一个受众；指向本 MCP 端点的其它写法按规范形式比较）。请求了未登记的
   `resource` 或一次请求两个受众 → `invalid_target`。刷新令牌只能用一次：每次刷新返回后继者，再次出示已用过的会撤销整组授权。
7. 之后每次请求带 `Authorization: Bearer <access_token>`。被拒绝时 401 / 403 都带 `WWW-Authenticate: Bearer …,
   resource_metadata="…"`，错误码只在据此行动有用时才写：
   * 401（令牌过期、撤销、受众不对）：`error="invalid_token"`——重新授权；
   * 403 且重新授权能拿到所缺 scope：`error="insufficient_scope", scope="<scope>"`——按它升级权限（step-up）。两种情形：调用了
     令牌 scope 不覆盖的工具（见 §2.5），或令牌一个可用的业务 scope 都没有（例如只有 `openid`），此时 `scope` 是该客户端登记里
     的默认业务 scope；
   * 其它 403（策略关闭、不再是成员、工作空间不可用、登记已不包含所缺 scope……）：不写错误码——新令牌或更多 scope 都改变不了
     结果，客户端不应再把本人送去授权。

### 2.4 同意页与工作空间选择

一枚令牌只属于一个工作空间。`platform` 应用的客户端申请了需要同意的 scope 而 `authorize` 没带 `workspace_id` 时，同意接口
（`GET /api/auth/oauth-consent/{rid}`）返回 `workspace_choices: [{id, name, role, consented}]`，本人选一个，批准
（`POST …/approve`，JSON `{"workspace_id": …}`）；给了选择而没选是 400 `workspace_required`。例外：本人此前对该客户端只在一个
在用空间同意过且覆盖所申请的 scope → 直接用它，不再问；只有一个可用空间 → 直接用。带了 `workspace_id` 的请求不给选择，
也不接受换成别的。同意接口还返回 `client.registration`（`operator` / `cimd`）与 `client.client_uri`，让本人看清是谁在申请。

同意页（`/oauth/consent`）在有选择时列出这些工作空间（单选，标出已授权过的），没有选择时「批准」不可用。自描述登记的客户端
会说明「这个客户端用它自己发布在 {主机} 的文档描述自己。Synwevo 读取并检查了这份文档，但没有核实是谁在运营它。」，其中的
主机是 `client_id` 的主机——账户服务拉取文档的地方；不用文档自称的 `client_uri`。

权限是三者的交集，每次调用重新计算：客户端登记的 scope ∩ 本人同意的 scope ∩ **本人此刻**在该空间的角色与应用资格。
查看者拿着 `forms.write` 也写不了。撤权规则与 REST 相同（本人在「已授权的应用」撤回、令牌撤销、客户端停用、被移出空间……
下一次请求即 401 / 403）。

### 2.5 工具、资源与调用语义

* **工具 = 操作。** `tools/list` 列出本人在该空间可用且令牌 scope 覆盖的业务操作（名字就是操作 id，如 `forms.form.list`；
  输入 schema 是操作自己的，加可选 `idempotency_key` 与 `correlation_id`；输出 schema 是信封
  `{run_id, operation, status, output, replayed, workspace_id, truncated}`），加两个平台工具 `synwevo.whoami`、
  `synwevo.run.get`。顺序确定，列表 `ttlMs = 60000`、`cacheScope = "private"`。注解按操作声明填：`readOnlyHint` = 无副作用，
  `destructiveHint` = 有副作用且 id 以 `.delete/.archive/.cancel/.withdraw/.revoke/.close` 结尾，`idempotentHint` = 只读，或
  必须带 `idempotency_key` 的操作（键可选的写操作不标：不带键重试会再执行一次），`openWorldHint` = 触达平台外的人。`_meta`
  里有 `com.synwevo/scope`、`app`、`side_effect`、`mode`、`version`。
* **先调 `synwevo.whoami`**：本人、客户端、工作空间、角色、scope、可用应用、到期时间。
* **调用 = `Capabilities.invoke(via="api")`**：校验、本人实时角色、运行记录、幂等、事件与 REST 完全一致。有副作用的操作可带
  `idempotency_key`（1–128 个可见 ASCII 字符）；会向他人发出请求、通知或链接的操作**必须**带（缺少是 `idempotency_key_required`）；
  同键同参重放第一次的结果（`replayed: true`），同键不同参 `idempotency_conflict`。键按客户端命名空间存储，与 REST 同一规则。
* **缺 scope**：调用令牌 scope 不覆盖的工具（或用 `synwevo.run.get` 读这样一个操作的运行），在执行之前答 **HTTP 403**：正文是
  这条请求的 JSON-RPC 错误（`-32602`，`data.code = "insufficient_scope"`、`data.required_scope`），官方 SDK 的客户端把它作为该
  请求的错误抛出；重新授权能拿到时 challenge 为 `insufficient_scope`，`scope` 写出已持有与所需的全部 scope，升级之后别的工具
  不会失去权限；拿不到时（客户端登记不包含它）`data.reason = "scope_not_registered"`，challenge 不写错误码。格式本身不对的
  请求先按格式拒绝（400 / 406 等），不发 challenge。
* **异步操作**不是完成：`status` 为 `queued` / `running` 时用 `synwevo.run.get` 轮询 `run_id`（只回答本客户端为本人在本空间
  发起的运行）。
* **结果**：`structuredContent` 是信封，`content` 的文本块是同一份 JSON。超过 400 000 字符时信封保留，只有 `output` 换成
  `{preview, note}`（`output` 的 JSON 文本能放下的最长前缀），并 `truncated: true`——仍符合工具声明的输出 schema（`output` 允许这个
  形状），`run_id` 仍在；没有 `output` 的结果整个换成 `{preview, note, truncated: true}`。`_meta["com.synwevo/request_id"]` 是请求
  编号。
* **错误**：业务层的拒绝是 `isError: true` 的结果，正文是 REST 的错误契约 `{"error": {code, message, request_id, details}}`
  （`invalid_input` 带 `details.fields`）；未知工具名是 JSON-RPC `-32602`。
* **资源**：`synwevo://whoami`（同上）与 `synwevo://operations`（本人在该空间可用的全部操作契约 + 每条 `granted`：令牌是否持有
  它需要的 scope），`application/json`。**没有提示词**（§6）。
* 每个 POST 一行 `api_platform_calls`（route `/api/suite/v1/mcp`），进 API 控制台的调用记录；每次业务调用一行 `op_runs`
  （`via="api"`，`delegation.transport="mcp"`）与工作空间审计，署名为本人并注明客户端；缺 scope 被拒的调用只记 403
  `insufficient_scope`，不进 `op_runs`。速率限制与 REST 共用（按凭据、按工作空间，429 `rate_limited`，`Retry-After`）。

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
| `SYNWEVO_MCP_RESOURCE` | **可选** | 覆盖入站的规范 URI / 令牌受众（默认 `{SYNWEVO_APP_URL}/api/suite/v1/mcp`，同样规范化）。只在端点对外地址与 `SYNWEVO_APP_URL` 不一致时才需要；challenge 与「连接你的 MCP 客户端」里的元数据地址随之推出，但应用只在固定路径 `/.well-known/oauth-protected-resource/api/suite/v1/mcp` 回答元数据，所以覆盖值的路径应仍是 `/api/suite/v1/mcp` |

没有新增必需变量。策略不读环境变量：开关与限额只来自已发布的策略版本。上面这些设置，连同已有的 `SYNWEVO_PUBLIC_URL`、
`SYNWEVO_PORTAL_URL`、`SYNWEVO_SITE_URL`、本进程监听的端口（`SYNWEVO_PORT`）和已登记应用的地址，也决定 Agent 把哪些地址视为本部署
自己的（§1.2）。

## 4. 管理与计量

管理接口 `/api/admin/mcp`（账户服务，`services/identity/synwevo_identity/admin_mcp.py`）：

| 路由 | 权限 | 说明 |
|---|---|---|
| `GET /policy`、`GET /policy/history` | `platform.config.view` | 当前策略（修订 0 = 内置默认）、默认值、JSON Schema；历史 |
| `POST /policy/preview` → `POST /policy/publish` | `platform.config.manage`，危险操作（严格重新验证）、理由 8–500 字、`idempotency_key`、`preview_token` | 与其它平台设置同一签名预览层；`expected_revision` 不等于当前 → 409 `mcp_policy_revision_conflict` |
| `GET /overview` | `platform.config.view` | 每个连接的遮罩视图（名称、归属、主机名、认证方式、状态、是否平台停用、是否在被停用的服务地址上（`destination_stopped`）、健康、最后错误码、失败连击、熔断、协议版本、工具 / 已授权 / 绑定人数、7 天调用与失败数），平台停用的排在最前；`stopped_destinations` 列出被停用关闭的服务地址 `[{host, since, connection_id, connection_name, other_connections}]`——只给主机，路径可能带着对方服务的密钥；合计含 24 小时出站调用数与入站请求数 |
| `GET /connections/{cid}/calls` | `platform.config.view` | 调用记录元数据（发起方、类型、名字、状态、错误码、时间、请求编号），**没有**预览 |
| `POST /connections/{cid}/disable` | `platform.config.manage`，危险操作（严格重新验证），理由 8–500 字 | 平台停用：记为平台的决定（所有者解不开、也不能删除，§1.2），状态 `disabled`，并关闭它的服务地址——解除之前任何连接都到不了那里；忘掉连接持有的令牌、OAuth 客户端密钥与动态登记（预登记的 `client_id` 保留）、进行中的认证；绑定保留但产出为空。审计 `admin.mcp.connection.disable` |
| `POST /connections/{cid}/lift` | 同上 | 解除平台停用：连接回到所有者手里，仍是停用、没有凭据，由所有者启用并重新认证；它的服务地址随之重新开放（另有停用关闭它的除外）；没有被平台停用 → 409 `mcp_not_platform_disabled`。审计 `admin.mcp.connection.lift` |

策略字段与默认值见设计记录 §5。平台管理员读不到任何连接的凭据、调用参数或结果。管理台的「MCP」页
（`services/identity/web/src/governance/McpTab.tsx`，已挂入管理台）调用这些接口：查看要 `platform.config.view`，发布、停用与
解除要 `platform.config.manage` 并重新验证。概览与调用记录在读取前把中断的调用整理为 `unknown`（§1.9）。

**计量。** 出站以 `mcp_calls`（次数、时长、状态、错误码）为唯一来源，Agent 发起的另有 `op_runs`（`agent.mcp.tool.read` /
`tool.call` / `resource.list` / `resource.read`）与审计；入站每个 POST 一行 `api_platform_calls`，每次业务调用一行 `op_runs`。
**它们不是模型 token**，不进任何模型用量或费用；外部服务是否收费本平台不知道，也不编造第三方成本。

## 5. 回滚

* **功能级**：发布一版策略把 `outbound_enabled` / `inbound_enabled` 关掉即停止一切，无需重启——出站每个入口先查
  `outbound_enabled`，入站网关先查 `inbound_enabled`。已有连接、授权、绑定、记录原样留在库里。
* **代码级**：表与列都是加法（`CREATE … IF NOT EXISTS`、`ALTER TABLE … ADD COLUMN`），老代码不读它们。这个分支新增的列：
  `mcp_connections.platform_disabled_at`、`platform_disabled_by`、`failure_users_json`、`refresh_claim_until`、`last_error_cause`
  （最后一次失败是否发生在授权服务器刷新令牌时，最新一轮加入），`mcp_connection_tools.unusable_code`，另有 `mcp_calls` 的部分索引
  `mcp_calls_running`。其余新记录放在已有的列里：`oauth_client_json` 的 `issuer`（预登记客户端绑定的授权服务器）与 `step_up`，
  认证流程的结果 `refused`；被停用关闭的服务地址由仍在的停用行推出，没有新表。去掉 `synwevo_suite/app.py` 与
  `agent/__init__.py` 里的两处 `install` 调用和 `mcp` / `mcp-types` / `jsonschema` / `referencing` 依赖即可卸下，数据不丢；再装回去
  继续可用。退回到加这些列之前的代码时：平台停用只剩 `status='disabled'`，所有者可以再启用；熔断不再按人计，令牌刷新不再
  只有一个调用方；不可用工具保存的占位 schema 在老代码里同样校验不过（`mcp_schema_invalid`），不会被执行。只退回最新一轮修复
  （回到 `4d78bed6`）时，上面那些新列与新键被忽略，服务地址的停用、组织门、预登记客户端的绑定、元数据文档对 `iss` 的要求、工作
  空间连接结果的数据流规则都随之没有——只宜在关掉出站时这样做。
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
| 出站：超过 500 条的发现列表 | 只保留前 500 条，截断只写进服务端日志，页面不显示（§1.4） |
| 工作空间内容出站的判断 | 只看空间与组织的数据流出站策略是否为 `any`，不按工具、不按内容分级；唯一的例外是空间连接自己的结果回到同一连接、同一地址（§1.7） |
| 经动态登记的混淆攻击 | **未挡住**：恶意服务的登记端点交回它以 Synwevo 名义在另一台开放动态登记、不回传 `iss` 的服务器上登记的 id 时（§1.3）。元数据文档已只用于回传 `iss` 的服务器，预登记客户端绑定它所属的授权服务器；对所有动态登记要求 `iss` 会拒绝今天大多数只提供动态登记的服务器 |
| 对话里的外部工具卡片 | 不显示 `mcp.withheld`（§1.7）；工具调用的错误只记代码与消息，所以授权服务器刷新失败、step-up 等仍按代码说明，集成页才按细节说明 |
| 运行中的 step-up | 缺 scope 的工具调用答 HTTP 403 与 `insufficient_scope` challenge（重新授权能拿到时），见 §2.5；缺 scope 的操作本来就不出现在 `tools/list` |
| 真实模型 + 真实外部 MCP 服务 | **未做**；现有证据见 §7 |

## 7. 验证

下列测试在提交 `4d78bed6` 上本地运行通过。括号里是测试函数数，以及参数化展开后的用例数。复核轮的文件，以及本轮改过的
`test_agent_mcp_connections.py`、`agent-integrations.test.ts`、`admin-mcp.test.mjs`，计数与结果是尚未提交的工作树上的
（2026-10-25 重跑：Python 退出码 0、无 FAILED / ERROR，前端全部通过）。

| 内容 | 位置 | 性质 |
|---|---|---|
| 元数据与 401 challenge 把客户端引向授权服务器；REST 令牌与 MCP 令牌互不通用；工具随 scope 增减、调用就是操作（创建 + 幂等重放 + 列表 + `run.get`）；错误是 `isError` 结果，缺 scope 是 SDK 收到的协议错误；未知工具是协议错误；两个资源；运行记录 `delegation.transport="mcp"`；2025 年代客户端经 `initialize` 握手走通；查看者拿着 write scope 也写不了；策略关闭即 403；别的主机名不是本服务 | `apps/suite/tests/test_mcp_server.py`（7 项） | 官方 SDK 客户端打真实应用（ASGI 传输），经真实网关；令牌直接铸造 |
| 截断保留信封并符合输出 schema；POST 之外答 405；缺 scope 的 403 与 step-up 何时给、何时不给；格式不对的请求先按格式拒绝；没有令牌时 challenge 不写错误码；`idempotentHint` 规则；规范受众 | `apps/suite/tests/test_mcp_server_conformance.py`（17 项，28 例） | 官方 SDK 客户端与原始 HTTP，打真实应用 |
| 连接测试是真实握手；模型找到、调用并确认外部工具（只读 auto 直接执行且结果标为外部内容，写工具等确认卡）；工具错误与坏输入以内容到达模型；工作空间内容在策略不允许时不出站；归属与授权规则；工具形状变了授权作废；超时与熔断；策略关闭即隐藏；资源与提示词；重试添加或修改不写第二次 | `apps/suite/tests/test_agent_mcp.py`（10 项） | **夹具** MCP 服务端（官方 SDK 的 `MCPServer`，进程内 ASGI 传输）+ **脚本模型**。协议是真实的，网络不是 |
| 承载操作对模型、页面、脚本都不可用；读承载只执行 auto 且只读的工具；确认时与发出时再查数据流；运行记录与重放不再调用服务端；结果随工作空间连接的使用权，按经过的连接而非别名；空白描述、不可用的 schema、各种方言 | `apps/suite/tests/test_agent_mcp_carriers.py`（14 项，21 例） | 夹具服务端 + 脚本模型 |
| 改地址忘掉的一切；存储上限与不可用工具；看不到的连接答 404；本地拒绝不碰熔断；一个成员打不开共享连接的熔断；令牌只刷新一次（同进程与跨进程），被改动抢先的刷新存不下；中断的调用记为 unknown；本部署自己的地址；旧库得到新列 | `apps/suite/tests/test_agent_mcp_connections.py`（24 项，25 例） | 夹具服务端；部分直接调用服务层 |
| 资源元数据必须指向本服务；issuer 逐字相同（含末尾 `/`）；必须宣告 PKCE S256；密钥不发给别的授权服务器；发现、登记、回调、刷新期间的改动不留下任何东西 | `apps/suite/tests/test_agent_mcp_oauth.py`（15 项，19 例） | 夹具授权服务器与 MCP 服务端 |
| 慢 DNS 与读库不阻塞共享循环；响应边收边计、压缩体同样；分页到底、截断与不结束的列表；HTTP 状态决定错误码；并发上限随策略变化；本部署源站的判定（loopback 看端口、显式列出） | `apps/suite/tests/test_agent_mcp_runtime.py`（31 项，41 例） | 运行时直接测（夹具传输与解析器） |
| 真实登录 → 同意（含工作空间选择）→ 令牌端点（`resource` 为 MCP 端点）→ MCP 列表与调用 → 撤回同意即 401；SDK 的 `OAuthClientProvider` 按 403 challenge 升级、无可授予时不送人去授权；按 §2.2 公式登记的客户端拿到宣告的 `resource` 的令牌；Agent 连接夹具服务、脚本模型调用、确认后执行、记录齐全 | `tests/integration/test_mcp_end_to_end.py`（7 项） | 组装后的部署（`synwevo_server.app.create_app`，角色 `all`）；夹具服务端 + 脚本模型 |
| Agent 出站 OAuth 完整回合：以 Synwevo 自己的 MCP 端点为外部服务（夹具策略在 `allowed_hosts` 里精确列出该主机），发现 → 预登记 client_id → 同意（选工作空间）→ 回调核对 `state`/`iss` → 换码 → 令牌组密封 → 用它发现工具 → 强制过期后刷新 → 撤销凭据；回调状态不符、`iss` 不符、重用 state 都拒绝；不列出时保存与使用都是 `mcp_destination_is_self` | `tests/integration/test_mcp_outbound_oauth.py`（3 项） | 组装后的部署；运行时经进程内 ASGI 传输到达本部署 |
| 平台停用对所有者成立直到解除；停用忘掉一切凭据；停用的连接不被认证、它的服务端收不到请求；登记期间落下的停用不留密钥；管理台整理中断的调用 | `tests/integration/test_mcp_admin_controls.py`（5 项） | 组装后的部署；夹具服务端与授权服务器 |
| RFC 8414 元数据 = Discovery + MCP 事实；每个授权响应带 `iss`；工作空间选择（必选、记住、扩 scope 再问、带 `workspace_id` 不问）；客户端元数据文档只在策略允许的域下登记公共客户端、24 小时内不重拉、未列回调拒绝；五种坏文档什么都不登记；文档只从公网 https 拉取；MCP 客户端申请全部宣告的 scope 时收窄到登记范围、非 MCP 客户端照旧拒绝未登记 scope | `services/identity/tests/test_oidc_mcp.py`（8 项，12 例） | 真实登录、同意与令牌端点；**夹具**替代文档的网络拉取 |
| 撤回机制或域即结束已存登记、令牌停止工作；过期登记重新拉取、拉取失败下次再试；匿名请求什么都不拉取；运维登记不受文档策略约束；`client_uri` 只留同源的，同意接口不传外源主页 | `services/identity/tests/test_cimd_policy.py`（8 项，15 例） | 真实授权、同意、令牌与 userinfo 端点；夹具文档 |
| 契约里多出 `/api/suite/v1/mcp`（标签 `mcp`，含 405 与 401 / 403 challenge） | `apps/suite/tests/test_machine_contract.py`、`tests/integration/test_suite_machine_contract.py` | 契约漂移测试 |
| 同意接口返回 `client.registration` / `client_uri` | `services/identity/tests/test_oauth_consent.py` | 既有测试随形状更新 |
| 集成中心、执行卡片、Agent 设置一节在三种语言下的渲染与交互规则：错误按错误码判断、丢失回应的创建不变成第二次、stale 授权按原模式重授、中断调用显示「结果不明」、平台停用与改地址后的下一步、不可用工具只能为 off；被停用的地址；令牌随显示的地址发送；按拒绝的细节说明（刷新失败在授权服务器、step-up、授权为何无法开始）；工作空间连接的结果是空间内容；预登记客户端写明并只交给它所属的授权服务器 | `apps/suite/web/tests/agent-integrations.test.ts`（39 项，其中 16 项按三种语言展开，共 71 例） | 服务端渲染 + 夹具数据 |
| 管理台 MCP 页（策略、遮罩概览、停用与解除、被停用的服务地址只列主机、调用记录、历史、措辞）；同意页的自描述说明写 `client_id` 的主机 | `services/identity/web/tests/admin-mcp.test.mjs`（12 项）、`services/identity/web/tests/oauth-consent-cimd.test.mjs`（4 项） | 服务端渲染 + 夹具数据 |
| 第三、四轮复核：组织门、凭据所对应的地址、目的地停用、刷新的结果与围栏、结果裁到运行记录、执行过却没记下是「结果不明」、工作空间连接的结果是空间内容（扣下时说明是哪个空间）并可回到同一连接、混淆攻击（元数据文档只用于回传 `iss` 的服务器）、预登记客户端绑定授权服务器、回调到最后仍须能决定、step-up、issuer 原样、解析器忙、入站的 issuer / 格式校验 / 末尾 `/` / 令牌端点的 `resource` 写法 | `apps/suite/tests/test_agent_mcp_round3_{connections,engine,oauth}.py`、`test_agent_mcp_round4_{connections,engine,oauth}.py`、`test_mcp_server_round3.py`，`services/identity/tests/test_oidc_mcp_round3.py`，`tests/integration/test_mcp_round3_admin.py` | 在这两轮修复的工作树上运行通过，尚未提交；内容与用例数见 [`../platform/mcp.md`](../platform/mcp.md) §9 |
| 真实 HTTP：官方 SDK 客户端（2026-07-28 与 2025-11-25 两种握手）、SDK 自己的 `OAuthClientProvider` 完整授权、Claude Code 2.1.146 连接；Agent 经钉住传输连接真实外部 MCP 服务（官方 SDK 服务端，回环） | `docs/agent-work/evidence/2026-10-24-mcp/` | 本机隔离实例（`SYNWEVO_TEST_INSTANCE=1`），模型为规则替身；记录于后端提交 `20f0de53`，早于本轮修复，之后没有重跑；目录里没有浏览器走查的记录 |

**没有的证据**：真实模型驱动的工具调用（证据里的模型是测试替身）；第三方运营的公网 MCP 服务；钉住传输对公网 DNS 与 TLS 的
行为（证据只覆盖回环 http）；客户端元数据文档的真实网络拉取；本轮修复之后的真实 HTTP 与浏览器走查；生产部署。
