# 平台架构：一套账户，多个应用

本轮把 Synwevo Docs 已有的身份体系升级为**平台级统一账户**，Synwevo Docs 成为平台里的第一个业务应用。
本文件记录职责划分与关键选型**依据**，不是选型比较报告。

状态：**已实施**；目录结构已在后一轮重组，见 §1 与 [`monorepo.md`](monorepo.md)。
每个区块的真实进度见 §7 与 [`docs/quality/progress.md`](../quality/progress.md)。

---

## 1. 仓库结构：这一节已被后一轮取代

**当时的决定**：不新建仓库、不搬目录，在同一个仓库内新增模块。理由是——把 Synwevo Docs 整体搬进
`apps/docs` 会在同一次改动里同时动到数据库路径、静态资源 base、导入路径和启动方式，
而这四样恰好是"用户数据看起来不见了"最常见的来源。

**现在的状态**：目录已经搬了（2026-09-24 的平台单仓库重组轮）。上面那个顾虑没有被取消，它被
拆成了四个分开验证的步骤：目录改名与数据位置分开处理、纯移动与逻辑修复分成两个提交、每完成一个
边界就跑针对性验证、迁移前后逐表逐 id 逐 blob 比对。结果记在
[`docs/migrations/platform-restructure.md`](../migrations/platform-restructure.md)，
现在的结构在 [`monorepo.md`](monorepo.md)。

这一节保留原文的意图，是因为那条顾虑本身是对的，只是它应该得到"拆开验证"这个回应，而不是
"永远不做"这个回应。

**没有变的**是这一条：**唯一的账户权威来源仍是现有 `users` / `user_passwords` / `sessions` /
`webauthn_credentials` / `totp_secrets` 等表。** 平台化不引入第二套密码库，也不引入第二份用户表；
重组同样没有。

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
| Synwevo Workspace（OIDC Client） | `http://portal.localhost:8769` | `dw_session`（host-only，门户域） |
| Synwevo Docs（OIDC Client） | `http://docweave.localhost:8765` | `dw_session`（host-only，Synwevo Docs 域） |
| Synwevo Message（OIDC Client，CI-66） | `http://message.localhost:8768` | `dw_session`（host-only，消息应用域；生产 `message.wegreen.ltd`） |

同名 Cookie，四个不同的主机，因此是四个互不相通的值——这正是要验证的性质。
所有 Cookie **不设 `Domain` 属性**（host-only），按 RFC 6265 不会发往兄弟主机。

`python scripts/platform.py up` 启动这三个进程，`python tests/integration/verify_sso.py` 用
Python 的 `http.cookiejar`（真实规则：按主机作用域、无视端口）把结果量出来。**已实测 32/32 通过**，
其中直接检查了：三个会话值互不相同、Synwevo Docs 的会话 Cookie `domain=docweave.localhost` 且
非 `.`-前缀、Cookie 没有端口限定、没有任何 Cookie 落在 `localhost` 这样的共享父域上。

**浏览器也走通了。**（此前记录的「沙箱浏览器只放行一个 `*.localhost` 主机」这一限制，本轮
重新尝试后不再成立，故更正。）在真实浏览器里完整走过：

1. 门户 → 点「登录」→ 账户服务输入一次口令 → 回到门户首页，看到 Synwevo Docs 卡片；
2. 点卡片 → 打开 Synwevo Docs 工作台，不再要求口令；
3. 平台登出 → 直接访问 `docweave.localhost:8775/capabilities` → 跳到账户服务登录 →
   **回到 `/capabilities` 本身**，不是回到首页。

浏览器验证发现了两处 HTTP 脚本测不到的问题，见下面 §3.3。

### 3.1 服务要监听自己域名解析到的地址

`*.localhost` 在本机同时解析到 `::1` 和 `127.0.0.1`，`getaddrinfo` 先返回 `::1`。uvicorn 只绑
一个地址，绑 `127.0.0.1` 会让 `[::1]:8770` 拒绝连接。浏览器和 urllib 会挨个地址重试，所以
浏览器跳转全部正常；httpx 不会，于是**只有服务端换码那一步失败**，表现为回调处的
`Server disconnected without sending a response`。`scripts/platform.py: bind_addr()` 因此按域名
解析结果绑定。生产同理：发行者域名和监听地址必须对得上。

### 3.2 不继承环境里的代理

连接身份服务的 HTTP 客户端设 `trust_env=False`。httpx 默认遵循 `HTTP_PROXY`，而这个连接上跑的是
授权码和客户端密钥；`NO_PROXY=localhost,127.0.0.1` **不匹配** `accounts.localhost`。本机验证时
就是这样被静默送进代理并丢弃的。需要经代理访问发行者的部署用 `SYNWEVO_OIDC_PROXY` 显式指定。

### 3.3 只有浏览器能发现的两件事

**一个二进制三个角色，外壳也要分三个。** 账户服务原本和 Synwevo Docs 共用同一个 SPA 外壳，于是
`accounts.localhost` 渲染出 Synwevo Docs 的项目列表，然后被自己的角色边界拒绝，页面上浮出
「该接口不在账户服务上」——一次**正确的拒绝被呈现成一个坏掉的页面**。现在是三个独立的 Vite 构建，各有自己的静态资源目录：`apps/docs/web`（工作台）、
`apps/portal/web`（门户）、`services/identity/web`（账户与安全），由角色决定服务哪一个；
单进程部署同时提供工作台与账户两个外壳，账户路径归账户外壳。

**依赖方不能有自己的口令框。** 点门户上的 Synwevo Docs 卡片，落到了 Synwevo Docs 自己的登录表单，
要求再输一次口令——而 `role=app` 的进程根本不提供 `/api/auth/login`，那个框永远不可能成功。
现在 `/api/auth/state` 里的 `password_login` 由角色决定（只有 `accounts` 和 `all` 为真），
依赖方的 `RequireSession` 和 `/login` 都直接前往 `/auth/start`。这件事重要的地方不只是它坏了：
一个收集口令却无法校验口令的表单，恰恰出现在「口令只在一个地方输入」这条规则最该被贯彻的页面上。

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

迁移时把现有合法成员显式映射为 Synwevo Docs 的应用资格，保住他们原有能力；**不顺带授予其他应用**，
也不因为新增资格表把老用户锁在外面。

## 6. 机器调用不走这条路

`/api/v1` 的 API 密钥机制保留。ID Token **不能**当作 Synwevo Docs API 的通用凭证——API 只接受
为自己受众签发、权限恰当且当前有效的凭据。机器身份与用户委托分别建模，授权精确到
app / workspace / 操作。机器配置只经 CLI 与配置文件管理，**产品 UI 不新增任何自动化入口**。

## 7. 本轮实际进度

| 区块 | 状态 |
|---|---|
| 架构决策与本机拓扑（本文件） | 已确定；`*.localhost` 解析、绑定地址、代理行为均已实测 |
| 应用注册与 OIDC 客户端数据模型 | 已完成（schema v4 追加表 + `V4_COLUMNS` 无条件列迁移） |
| Provider 端点（discovery/JWKS/authorize/token/userinfo/revocation/logout） | 已完成并测试 |
| 刷新令牌轮换 + 重放检测 | 已完成并测试 |
| Synwevo Docs 接入为 Client（BFF） | 已完成并测试；本机三来源链路 38/38 通过，浏览器亦已走通 |
| 跨应用登出（后通道）与撤权传播 | 已完成并测试 |
| 角色边界（accounts / portal / app 三个角色） | 已完成；本机实测三种 404 边界 |
| CLI：应用、客户端、密钥 | 已完成并本机跑通 |
| 门户前端页面 | 已完成：登录、空状态、应用卡片、工作空间切换、账户入口；浏览器实测 |
| 账户服务独立外壳（`services/identity/web`） | 已完成；账户服务不再渲染业务工作台。重组后账户与安全界面**全平台只有这一份** |
| 真实库迁移演练（69 项目副本） | 已完成 15/15；来源只读，`default` 仍 0 成员 |
| 平台管理后台 | 已完成：概览、账户（停用/恢复/撤销会话）、工作空间、审计检索；后端 21 条测试 |
| 历史数据认领 | 已完成；在真实 69 项目库的隔离副本上完整走通：认领 → SSO → 看到 68 个历史项目 |
| Synwevo Docs 内的平台入口 | 已完成：返回平台、工作空间切换、账户入口；单进程部署上不渲染 |
| Passkey / WebAuthn | 已完成：登记、登录、改名、删除、域名变更提示；19 条测试用软件认证器驱动。真实浏览器 + 真实认证器互操作未验证（需用户生物特征，不擅自发起） |
| 机器身份（服务账户 / 用户委托） | 已完成；本机实跑：取令牌 → 提交任务 → 取回 10 个真实 .docx，全程无浏览器 |
| 找回口令与邮箱验证 | 已完成；20 条测试。本地投递目录验证，生产邮件送达属外部条件 |

**未部署到服务器。** 本轮全部在本机完成，未修改生产 DNS，未创建隧道，未公开暴露服务。

## 8. 登录后的落点（2026-09-27）

账户服务只负责认证；工作在平台（Synwevo Workspace）里。因此**没有明确返回目标的登录，落点是平台，不是账户设置页**：
用户反馈（2026-09-27）在 `accounts.wegreen.ltd` 登录后直接停在 `/account` 被理解为"逻辑有重大错误"——
登录是为了去工作，账户设置在顶栏一键可达。参照：Microsoft Entra 的登录门户把已登录者放到应用启动页 My Apps
（"When signed in to the My Apps portal, the applications that are made visible are shown"，learn.microsoft.com
`entra/identity/enterprise-apps/myapps-overview`，访问 2026-09-27），而不是账户资料页。

| 情形 | 落点 | 实现 |
|---|---|---|
| 从门户或应用发起（`/auth/start?next=…` → `/login?next=/oidc/authorize…`） | 回到发起方的 `next`（不变） | `signedInDestination` 尊重明确的 `next` |
| 在账户服务直接登录、`/logged-out` 的「重新登录」、忘记口令后重新登录（`next` 为空或 `/`） | 平台工作空间：`<portal_url>/auth/start?next=/workspace&lang=<界面语言>` | `platformHomeUrl`（`@synwevo/auth-client` 的 `platformHandoffUrl`） |
| 已登录者打开账户服务根路径 `/` | 同上 | `main.tsx` 的 `Home` |
| 明确要去账户页（`/login?next=/account`、门户账户入口的 `?from=platform`） | 账户页 | 不变 |
| 邮箱未验证 / 有进行中的邀请 / 未完成引导 | `/verify-email` / `/invite` / `/onboarding`（先于一切） | 不变 |
| 部署没有门户（`SYNWEVO_PORTAL_URL` 未设，Docs 单独自托管） | `/account`（唯一的落点） | `platformHandoffUrl` 返回 `null` 时回退 |
| Docs 的「← Synwevo」、账户外壳的品牌链接 | 工作空间：`<portal_url>/auth/start?next=/workspace&lang=…`（用户反馈 2026-09-27：曾回到公开首页） | `PlatformNav` / `Topbar` 用 `platformHandoffUrl`（CI-62） |
| 在账户服务点「退出登录」 | 中央会话结束，后通道通知各应用，落到账户服务 `/logged-out`（已退出 / 重新登录 / 返回平台首页） | `signOut()` → `/api/auth/logout` |
| 在依赖方（Docs、门户）点「退出登录」 | 同样结束平台会话：依赖方撤本地会话并交出账户服务登出地址 → `/oidc/logout` → 回到本源 `/signed-out`（同一页面语义）；一个账户在哪里退出都是退出 Synwevo。门户的退出入口在页头 / 手机账户菜单（CI-61） | `signOut()` → `/api/auth/sign-out?everywhere=true`（CI-60） |

落点经过门户自己的 `/auth/start`，而不是直接链到门户首页：这样门户在同一跳里拿到自己的会话，
渲染工作空间时不必再回账户服务一次。门户地址来自服务端配置（`/api/auth/state` 的 `portal_url`），
从不来自查询参数或 Referer，因此不构成开放重定向；配置成非 http(s) 绝对地址时该辅助函数抛错
（引导页的按钮显示为错误），登录页则回退到 `/account`，不让配置错误把登录页拖垮。
`/logged-out` 页同时给出「返回平台首页」的次级入口。验证：`platform-handoff.test.mjs`（5 项）、
`flow.validation.mjs`、`ui_e2e_shell.py`（Chromium/WebKit：无目标登录 → 门户 `/workspace`；账户根路径 → 门户；
`/login?next=/account` → 账户页）。

