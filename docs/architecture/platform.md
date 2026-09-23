# 平台架构：一套账户，多个应用

本轮把 DocWeave 已有的身份体系升级为**平台级统一账户**，DocWeave 成为平台里的第一个业务应用。
本文件记录职责划分与关键选型**依据**，不是选型比较报告。

状态：**实施中**。每个区块的真实进度见 §7，未完成的不写成已完成。

---

## 1. 为什么不新建仓库、不搬目录

当前 Git 根目录就是 `/Users/lihiko/repo/wegreen/DocWeave`（`0d3c500`），数据在 `data/`（56 MB，
含 69 个真实项目）。本轮**在同一个仓库内新增模块**，不动 remote、不改仓库名、不建嵌套仓库。

更重要的是**不把 DocWeave 整体搬进 `apps/docweave`**。那样做会在同一次改动里同时动到数据库路径、
静态资源 base、导入路径和启动方式——而这四样恰好是"用户数据看起来不见了"最常见的来源。
目录整齐不值得拿这个换。边界靠模块和接口划清，将来真要拆分时再单独迁移并验证。

| 内容 | 实际位置 | 说明 |
|---|---|---|
| DocWeave 业务 | `backend/docweave/`、`frontend/src/` | **原地不动** |
| 平台账户服务 | `backend/docweave/platform/` | 与业务模块同进程可启动，也可独立启动 |
| 平台门户 | `frontend/portal/` | 独立 Vite 入口，不吞掉 DocWeave 的前端路由 |
| 数据 | `data/`（原位） | 不新建第二份账户库 |
| 文档 | `docs/architecture/`、`docs/security/`、`docs/integration/` | — |

**唯一的账户权威来源仍是现有 `users` / `user_passwords` / `sessions` / `webauthn_credentials` /
`totp_secrets` 等表。** 平台化不引入第二套密码库，也不引入第二份用户表。

## 2. 选型：Authlib 作为授权服务器，不自己写协议

**结论：`authlib==1.6.5`（BSD-3-Clause）。**

要求里有一条是硬的：不得手写简化版 OAuth/OIDC 或自创跨应用票据协议。所以真正的问题不是
"要不要用成熟实现"，而是"哪一种成熟实现不会逼我再维护一套账户权威来源"。

* **Keycloak / Ory Hydra / Authentik / Zitadel**：都是合格的 IdP，但它们自带用户存储。
  接进来只有两条路：把现有账户迁过去（那就得重做 Argon2id 参数决策、MFA 种子加密、会话语义、
  以及 69 个项目的归属映射），或者让它联邦到现有库（那就是长期维护两个权威来源）。
  §3.1 明确反对后者，而前者的风险与本轮"保住现有账户与数据"的目标直接冲突。
  它们还需要 JVM/Go 运行时与独立数据库，与"不要因为平台化就引入超出需要的集群设施"相悖。
* **Authlib**：是库不是服务。它提供 `AuthorizationServer`、`AuthorizationCodeGrant`、
  PKCE（RFC 7636 `CodeChallenge`）、`OpenIDCode`、`RevocationEndpoint`、`IntrospectionEndpoint`
  和 JOSE（JWT/JWKS），协议逻辑由库负责；**用户从哪来、口令怎么校验、MFA 怎么判定，仍然是我们
  现有的代码**。这正好满足"复用已完成的用户模型、口令校验和安全策略"。

已核实（非沿用历史描述）：

```
authlib 1.6.5 | BSD-3-Clause
AuthorizationServer / AuthorizationCodeGrant / rfc7636.CodeChallenge /
oidc.core.grants.OpenIDCode / rfc7009.RevocationEndpoint / rfc7662.IntrospectionEndpoint
```

**这不代表本项目通过了任何 OpenID 认证。** 我们用的是一个成熟实现，不是一张证书。

## 3. 本机拓扑：端口不隔离 Cookie，主机名才隔离

Cookie 按**主机**划分，不按端口。`localhost:8765` 与 `localhost:8766` 共享 Cookie，
所以"换个端口就算隔离"是错的。

本机采用 `*.localhost` 子域。现代浏览器与系统解析器都把它们解析到 127.0.0.1，**不需要改
`/etc/hosts`**（已实测：`accounts.localhost` / `portal.localhost` / `docweave.localhost`
经 curl 均返回各自的 Host），而它们对 Cookie 而言是**三个不同的主机**。

| 角色 | 本机地址 | 会话 Cookie |
|---|---|---|
| 平台账户服务（OIDC Provider） | `http://accounts.localhost:8770` | `dw_session`（host-only，账户服务域） |
| 平台门户（OIDC Client） | `http://portal.localhost:8769` | `dw_session`（host-only，门户域） |
| DocWeave（OIDC Client） | `http://docweave.localhost:8765` | `dw_session`（host-only，DocWeave 域） |

同名 Cookie，三个不同的主机，因此是三个互不相通的值——这正是要验证的性质。
所有 Cookie **不设 `Domain` 属性**（host-only），按 RFC 6265 不会发往兄弟主机。

`python scripts/platform_local.py up` 启动这三个进程，`python scripts/verify_sso.py` 用
Python 的 `http.cookiejar`（真实规则：按主机作用域、无视端口）把结果量出来。**已实测 32/32 通过**，
其中直接检查了：三个会话值互不相同、DocWeave 的会话 Cookie `domain=docweave.localhost` 且
非 `.`-前缀、Cookie 没有端口限定、没有任何 Cookie 落在 `localhost` 这样的共享父域上。

> **本机验证的已知限制**：本会话中的沙箱浏览器按主机授权，只放行了 `accounts.localhost`，
> 兄弟主机被拒。因此跨 origin 的重定向链路用 `http.cookiejar` 验证（上面那 32 项），
> 浏览器仅验证单 origin 页面。这一限制在交付报告中单独列出，
> 不写成"浏览器已验证跨域隔离"。

### 3.1 服务要监听自己域名解析到的地址

`*.localhost` 在本机同时解析到 `::1` 和 `127.0.0.1`，`getaddrinfo` 先返回 `::1`。uvicorn 只绑
一个地址，绑 `127.0.0.1` 会让 `[::1]:8770` 拒绝连接。浏览器和 urllib 会挨个地址重试，所以
浏览器跳转全部正常；httpx 不会，于是**只有服务端换码那一步失败**，表现为回调处的
`Server disconnected without sending a response`。`platform_local.py: bind_addr()` 因此按域名
解析结果绑定。生产同理：发行者域名和监听地址必须对得上。

### 3.2 不继承环境里的代理

连接身份服务的 HTTP 客户端设 `trust_env=False`。httpx 默认遵循 `HTTP_PROXY`，而这个连接上跑的是
授权码和客户端密钥；`NO_PROXY=localhost,127.0.0.1` **不匹配** `accounts.localhost`。本机验证时
就是这样被静默送进代理并丢弃的。需要经代理访问发行者的部署用 `DOCWEAVE_OIDC_PROXY` 显式指定。

生产域名未定，全部通过配置项给出，不写死。改域名会使已登记的 Passkey 失效（RP ID 绑定域名），
这一点记在 `docs/security/sessions.md`。

## 4. 概念分离：四个 id 不是一回事

| 概念 | 含义 | 例 |
|---|---|---|
| `workspace_id` | 数据归属边界，**等于 tenant** | `w_ab12…`、`default` |
| `app_id` | 平台里的一个业务应用 | `docweave` |
| `client_id` | 某个应用的 OIDC 客户端 | `docweave-web`、`portal-web` |
| API `audience` | 令牌被允许调用的 API | `docweave-api` |

一个应用可以有多个客户端（网页、将来的桌面端），它们共享 `app_id` 但各有 `client_id` 与回调。
**不复用、不混写。** 沿用既有的 `workspace_id = tenant`，不新造 `org_id`。

## 5. 权限分层：登录只证明身份

```
登录成功  ──►  这是谁（user_id，认证方式与时间）
                 │
                 ├─ 账户状态是否 active？
                 ├─ 是不是这个 workspace 的成员？角色是什么？
                 ├─ 这个 workspace 启用了这个 app 吗？
                 ├─ 这个用户对这个 app 有资格吗？
                 └─ 这个对象真的属于这个 workspace 吗？（authz.py，逐对象）
```

五层都过才放行。**平台管理员只治理账户与平台配置，默认读不到任何业务文档**——这条在本轮
不放宽；新增应用资格表也不得成为绕过 `authz.py` 的旁路。

迁移时把现有合法成员显式映射为 DocWeave 的应用资格，保住他们原有能力；**不顺带授予其他应用**，
也不因为新增资格表把老用户锁在外面。

## 6. 机器调用不走这条路

`/api/v1` 的 API 密钥机制保留。ID Token **不能**当作 DocWeave API 的通用凭证——API 只接受
为自己受众签发、权限恰当且当前有效的凭据。机器身份与用户委托分别建模，授权精确到
app / workspace / 操作。机器配置只经 CLI 与配置文件管理，**产品 UI 不新增任何自动化入口**。

## 7. 本轮实际进度

| 区块 | 状态 |
|---|---|
| 架构决策与本机拓扑（本文件） | 已确定；`*.localhost` 解析、绑定地址、代理行为均已实测 |
| 应用注册与 OIDC 客户端数据模型 | 已完成（schema v4 追加表 + `V4_COLUMNS` 无条件列迁移） |
| Provider 端点（discovery/JWKS/authorize/token/userinfo/revocation/logout） | 已完成并测试 |
| 刷新令牌轮换 + 重放检测 | 已完成并测试 |
| DocWeave 接入为 Client（BFF） | 已完成并测试；本机三来源链路 32/32 通过 |
| 跨应用登出（后通道）与撤权传播 | 已完成并测试 |
| 角色边界（accounts / portal / app 三个角色） | 已完成；本机实测三种 404 边界 |
| CLI：应用、客户端、密钥 | 已完成并本机跑通 |
| 门户前端页面 | 未开始（后端链路已通，`portal` 角色已可运行） |
| 管理后台与历史数据认领 | 未开始 |
| Passkey / WebAuthn | 未开始；RP ID 策略已确定为账户服务域名 |
| 机器身份接入 OIDC（服务账户 / 用户委托） | 未开始；现有 `/api/v1` 密钥体系不变 |

**未部署到服务器。** 本轮全部在本机完成，未修改生产 DNS，未创建隧道，未公开暴露服务。
