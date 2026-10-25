# MCP 双向真实验证（2026-10-24，本机隔离实例）

分支 `claude/mcp-platform`，后端提交 `20f0de53`。实例由 [`isolated_instance.py`](isolated_instance.py) 启动：
一个 `role=all` 的 Synwevo 进程（uvicorn，`http://127.0.0.1:33890`，数据目录在会话 scratchpad，
`SYNWEVO_TEST_INSTANCE=1`，`/healthz` 报 `instance=test`），加一个用官方 Python SDK（`mcp` 2.3.0）写的真实外部
MCP 服务（`http://127.0.0.1:33891/mcp`，工具 `forecast`/`add_note`，资源 `notes://all`，提示词 `summarise`）。

**什么是真的**：Synwevo 的全部栈（存储、网关、账户服务、业务应用、Synwevo MCP 服务端、Agent 引擎与其 MCP 客户端）；
外部 MCP 服务（SDK 服务端，经真实 HTTP 到达，走受检解析与钉住连接的出站传输，不是进程内替身）；MCP 客户端（SDK 的
`Client`，Streamable HTTP）。**什么是替身**：模型（规则替身，`isolated_instance.py` 的 `DemoModel`，问天气就调 `forecast`，
说记笔记就调 `add_note`）；入站探针的令牌是按账户服务同意后的方式直接签发的夹具令牌（浏览器 OAuth 流程本身由
`services/identity/tests/test_oidc_mcp.py` 与 `tests/integration/test_mcp_end_to_end.py` 覆盖）。**没有做**：真实模型、
真实第三方 MCP 服务、生产部署——本目录不是这些的证据。

| 文件 | 内容 | 结果 |
|---|---|---|
| [`inbound-sdk-client-2026-07-28.json`](inbound-sdk-client-2026-07-28.json) | SDK 客户端（协议版本 2026-07-28）经真实 HTTP：读取 RFC 9728 元数据；无令牌 → 401 + `WWW-Authenticate: Bearer … resource_metadata=…`；`tools/list` 21 个工具（按令牌 scope `forms.read forms.write` 过滤）；`synwevo.whoami`；`forms.form.create`（带 `idempotency_key`，再次调用 `replayed=true`）；`forms.form.list`；`resources/list`、`resources/read` | 全部成功，整段 0.07 s |
| [`inbound-sdk-client-legacy-2025-11-25.json`](inbound-sdk-client-legacy-2025-11-25.json) | 同一探针以 `mode="legacy"`（2025 年代 initialize 握手）连接同一端点 | 协商为 2025-11-25，同样 21 个工具、创建成功、幂等重放 |
| [`outbound-agent-to-external-server.json`](outbound-agent-to-external-server.json) | 以 alice 的真实会话经 `/api/suite/ops/*`：新建连接 → 测试（真实握手，协议 2026-07-28，2 工具 1 资源 1 提示词，首次个人发现的工具都是 `confirm`）→ `forecast` 改为 `auto` → 绑定到自己的 Agent → 一轮对话调用只读工具（无需确认，结果进入模型，`is_error=false`，2 ms）→ 一轮对话调用写入工具（停在确认卡，参数原样可见）→ 确认后执行、结果回到模型 → 页面读取资源与渲染提示词 → 调用记录（来源、种类、名称、状态、耗时） | 全部成功 |

探针脚本：[`inbound_probe.py`](inbound_probe.py)、[`outbound_probe.py`](outbound_probe.py)。令牌文件不在本目录。

## 第二个真实客户端：Claude Code 2.1.146

同一实例上，用 Claude Code 自己的 MCP 客户端（独立于 Python SDK 的实现）登记该端点：
`claude mcp add -s local --transport http synwevo-proof http://127.0.0.1:33890/api/suite/v1/mcp --header "Authorization: Bearer <夹具令牌>"`。
`claude mcp list` 报告 `synwevo-proof: http://127.0.0.1:33890/api/suite/v1/mcp (HTTP) - ✓ Connected`；网关的调用记录
（`api_platform_calls`，路由 `/api/suite/v1/mcp`）同时出现该客户端的 POST：200（initialize / tools/list）与 202（`notifications/initialized`，
即它以 2025 年代握手连接，由同一端点服务）。随后 `claude mcp remove -s local synwevo-proof` 撤销登记；本仓库与用户配置不保留它。

## 完整 OAuth：SDK 自己的 `OAuthClientProvider`

[`inbound-sdk-oauth-provider-full-flow.json`](inbound-sdk-oauth-provider-full-flow.json)，脚本 [`oauth_sdk_probe.py`](oauth_sdk_probe.py)。
官方 SDK 的 OAuth 客户端自行完成：收到 401 挑战 → 读 RFC 9728 元数据 → 读 RFC 8414 元数据 → PKCE S256 + `resource` 授权请求 →
回调核对 `state` 与 `iss`（RFC 9207）→ 换取令牌（含刷新令牌）→ 以该令牌调用 `synwevo.whoami` 与 `forms.form.create`，全部成功。
唯一的替身是「浏览器」这一步：脚本以夹具账户的口令登录、在同意页选择工作空间并批准，再把回调地址交还 SDK。

这一轮首次运行时**发现并修复了一个真实的互操作缺陷**：SDK 按 MCP 授权规范的作用域选择规则，请求受保护资源元数据列出的全部作用域；
授权端点此前拒绝客户端未登记的任何作用域（`invalid_scope`），于是一个只登记了部分作用域的标准客户端永远无法完成授权。
修复：面向 MCP 受众登记的客户端，授权请求收窄到其登记范围（RFC 6749 §3.3，令牌响应写明实际授予的作用域），没有剩余业务作用域时仍拒绝；
元数据默认只列读写作用域，`*.decide`（以本人名义作决定）需客户端明确请求。见 `services/identity/tests/test_oidc_mcp.py` 新增两项。
