# 单仓库结构：目录、依赖、数据所有权、独立运行边界

上一轮的判断是「不搬目录」，理由是一次改动同时动数据库路径、静态资源 base、导入路径和启动方式，
是"用户数据看起来不见了"最常见的来源（见 `platform.md` §1）。本轮把目录搬了，但没有取消那个顾虑：
它被拆成了四个分开验证的步骤，过程与证据在
[`docs/migrations/platform-restructure.md`](../migrations/platform-restructure.md)。

这份文件写**现在是什么样**。

---

## 1. 目录与归属

| 路径 | 职责 | Python 包 / npm 包 |
|---|---|---|
| `apps/docs/` | Synwevo Docs：文档引擎、业务接口、`/api/v1` 机器接口、工作台前端、应用专属测试与脚本 | `synwevo_docs` / `@synwevo/docs-web` |
| `apps/portal/` | Synwevo Workspace：登录后的第一页与应用入口 | `synwevo_portal` / `@synwevo/workspace-web` |
| `services/identity/` | Synwevo Account：登录仪式、MFA、Passkey、邀请与找回、平台管理后台、OIDC 授权服务器、**账户与安全界面** | `synwevo_identity` / `@synwevo/account-web` |
| `packages/core/` | 共享：`Store`（唯一的持久化入口与迁移执行者）、租户上下文、身份/会话/口令、机器身份、OIDC 令牌存储、HTTP 骨架、错误契约 | `synwevo_core` |
| `packages/auth-client/` | 应用接入统一身份：OIDC 依赖方（`python/`）、共用会话路由、浏览器端会话上下文与平台导航（`web/`） | `synwevo_auth_client` / `@synwevo/auth-client` |
| `packages/ui/` | 三个外壳共用的设计变量、主题、Toast/对话框、尺寸适配 | — / `@synwevo/ui` |
| `infra/server/` | 部署组装：按 `SYNWEVO_ROLE` 把上面的模块拼成一个进程 | `synwevo_server` |
| `infra/` | 容器构建与 Compose |
| `scripts/` | 根目录命令：安装、启动/停止/状态/日志、测试、备份、边界检查 |
| `tests/integration/`（含 `sdk_acceptance/`） | 跨模块验证：统一登录链路、机器身份、跨工作空间隔离、角色边界、外壳归属 |
| `docs/` | 平台文档 |
| `data/` | 运行数据（数据库 + blob）。**不进 Git** |

SDK 运行时留在 `apps/docs/sdk/`，只通过 HTTP 访问服务；会启动部署工厂、初始化身份夹具的 SDK 验收脚本
属于 `tests/integration/sdk_acceptance/`，不随客户端运行时包发布。

`packages/contracts/` 不存在，这是有意的：目前没有哪一组类型是三方共用而又不属于
`synwevo_core` 的。需要时再建，不为了对上一张表而先建空目录。

## 2. 依赖方向

```
                 synwevo_core
                 synwevo_auth_client
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
 synwevo_identity  synwevo_portal  synwevo_docs     ← 互相不可见
        └───────────────┼───────────────┘
                        ▼
                 synwevo_server                   ← 唯一可以同时导入三者
```

规则与理由：

* **应用不得导入另一个应用的内部实现。** 否则"独立运行"只是巧合。
* **应用不得导入账户服务的实现。** Synwevo Docs 是依赖方，经 OIDC 与它交互；它需要的读侧能力
  （成员、应用资格、令牌校验）在 `synwevo_core`。
* **共享包不反向依赖应用。** `synwevo_core` 里出现 `import synwevo_docs`，账户服务就再也不能在没有
  文档引擎的机器上启动。
* **`synwevo_server` 是例外，因为它不是应用而是部署方式。** 把它放在 `infra/` 而不是任何一个应用
  里，是为了让上面三条可以被机械检查——一个应用里藏着"顺便把账户服务也挂上"的代码，检查就只能靠人读。

```bash
python scripts/check_boundaries.py
```

同时检查：跨模块导入、`sys.path` 改写、写死的机器绝对路径、指向旧目录的符号链接。
检查范围与计数以命令当次输出为准，包含尚未提交的当前源码。

### 为什么 OIDC 的令牌存储在共享层

`synwevo_core.oidc` 放的是**数据模型与令牌存储**（签名密钥、客户端、授权码、访问/刷新令牌），
协议端点在 `synwevo_identity.provider_routes`。分界的理由很具体：**验证令牌的一方不是签发它的
一方**。Synwevo Docs 的 `/api/v1` 要在每次调用时判断一个访问令牌是否仍然有效、属于谁、还剩哪些权限。
如果这段代码住在账户服务里，业务应用就得 import 账户服务才能拒绝一个过期令牌——那条 import 一旦
存在，"账户服务可以独立于业务应用运行"就不再是可检查的事实。

## 3. 数据所有权与迁移执行者

**一个数据库**（`data/docweave.sqlite3`），因为账户权威只能有一个：两个库就是"这个人是否已停用"
的两个答案。目录重组不要求物理拆库。

| 表 | 所有者 | 谁写 |
|---|---|---|
| `users`、`user_passwords`、`sessions`、`webauthn_credentials`、`totp_secrets`、`recovery_codes`、`mfa_tickets`、`email_tokens`、`invitations`、`bootstrap_tokens`、`auth_attempts` | 账户服务 | `synwevo_identity` |
| `workspaces`、`memberships`、`apps`、`workspace_apps`、`app_entitlements`、`audit_events` | 平台（账户权威） | `synwevo_core`（读写），账户服务的管理后台与 CLI（治理） |
| `oauth_clients`、`oauth_codes`、`oauth_tokens`、`oidc_keys`、`client_sessions`、`rp_auth_requests`、`rp_logout_seen` | 平台身份协议 | 账户服务签发，`synwevo_core.oidc` 存取与校验 |
| `projects`、`source_files`、`templates`、`template_versions`、`mapping_revs`、`jobs`、`job_items`、`job_runs`、`job_events`、`artifacts`、`idempotency`、`webhook_deliveries`、`api_keys`、`blob_grants` | Synwevo Docs | `synwevo_docs` |
| `meta`、`ui_users`、`mail_outbox` | 平台基础 | `synwevo_core` / 账户服务 |

**schema 迁移只有一个执行者**：`synwevo_core.store.Store`。多个服务各自跑迁移，是同一个库出现
两套结构的最短路径。任何角色启动时打开 Store，迁移都由同一段代码、按同一个版本号执行。

`workspace_id = tenant` 的契约继续有效：业务表的 `tenant` 列就是工作空间 id，不新造 `org_id`。

## 4. 数据位置怎么解析

顺序：命令行 `--data` → `SYNWEVO_DATA_DIR` → 仓库根目录的 `data/`。

第三档由 `synwevo_core.http.repo_root()` 从**模块自身位置**向上找工作区标记目录得出，
**不依赖进程 cwd**，所以从平台根目录启动和从 `apps/docs/` 启动加载的是同一个库。
在源码检出之外（例如 site-packages 安装）没有可辩护的默认值，于是**报错而不是猜**：
猜中一个可写路径不会失败，它会建出一个空库、服务照常启动——那正是"数据不见了"的样子。

同样的道理，`scripts/platform.py up` 在数据目录里没有 `docweave.sqlite3` 时**拒绝启动**，
除非显式给 `--init`。

## 5. 独立运行边界

| 停掉谁 | 还能做什么 |
|---|---|
| 门户 | Synwevo Docs 的登录、工作台与自动化照常（`up accounts docs` 即可） |
| Synwevo Docs | 门户与账户页照常；门户按注册表如实显示应用状态 |
| 账户服务 | 依赖方无法建立新会话（既有策略），已签发的机器令牌按其自身有效期与撤销状态处理 |

"独立运行"**不等于**"复制一个 `apps/docs` 文件夹就能脱离一切"：它需要的共享包与外部服务
在 `apps/docs/pyproject.toml`、`apps/docs/requirements.txt` 和
`apps/docs/web/package.json` 里明确列着。

## 6. 角色与进程

一个进程一个角色，由 `SYNWEVO_ROLE` 决定，或由组装层显式传入：

| 角色 | 挂载 | 外壳 |
|---|---|---|
| `accounts` | 账户服务 + 共用会话路由 | 账户外壳（`account-assets/`） |
| `portal` | 门户 + 依赖方 + 共用会话路由 | 门户外壳（`portal-assets/`） |
| `app` | Synwevo Docs + 依赖方 + 共用会话路由 | 工作台外壳（`assets/`） |
| `all` | Synwevo Docs + 账户服务 + 依赖方 + 共用会话路由 | 工作台外壳 **和** 账户外壳（同一源站，容器镜像用这个） |

角色**在构建应用时确定**，不在每次请求时读环境变量。理由是条件挂载：进程里存在哪些路由，必须和
网关按角色拒绝哪些路径是同一个答案——两者可能不一致，比其中一个偶尔粗糙更糟。

角色边界仍由 `synwevo_core.http` 里**集中的网关**判定，而不是靠"哪些路由被挂上去了"。少挂一个
路由只会得到一个没有解释的 404；集中判定的网关无法被漏掉一半，账户服务也就真的不可能服务一份文档。

## 7. 前端：三个外壳，两个共享包

三个独立的 Vite 构建，**静态资源目录各不相同**（`assets/`、`portal-assets/`、`account-assets/`）。
单进程部署要同时提供工作台和账户两个外壳，同名的 `/assets` 会互相遮盖——那是"页面能打开但样式
没了"的来源。

**账户与安全界面只有一份**，在 `services/identity/web/`。任何角色下，`/login`、`/account`、
`/invite`、`/forgot-password`、`/reset-password`、`/logged-out` 都由账户外壳提供：

* `role=all`：两个外壳在同一个源站上，同源整页导航，Cookie 与接口不变；
* `role=app` / `role=portal`：这些路径是**服务端重定向**到账户服务，不是页面。
  一个收集口令却无法校验口令的表单，恰恰不该出现在"口令只在一个地方输入"这条规则最该被贯彻的页面上。
