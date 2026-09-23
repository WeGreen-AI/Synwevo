# 把一个应用接入平台账户体系

本文档是可执行的：每条命令都在本机跑通过，`python tests/integration/verify_sso.py` 会把结果量出来（本轮实测 38/38）。

平台账户服务是 OIDC Provider；平台门户和 DocWeave 都是接入它的独立客户端。本轮的两个客户端
都是真实业务链路的一部分，因此它们本身就是跨客户端验证的对象。

**没有产品界面。** 应用登记、客户端配置、回调地址、签名密钥、机器凭据全部通过 CLI 和配置管理。
这不是还没做，是不做：一个能修改回调地址的网页，就是一个能让授权码送到别处去的网页。

---

## 0. 概念先分清

这四个东西经常被混为一谈，混了就会出安全问题：

| 概念 | 是什么 | 例子 |
|---|---|---|
| `app_id` | 平台上的一个业务应用 | `docweave` |
| `client_id` | 该应用的一个 OIDC 客户端 | `docweave-web`（将来可能还有 `docweave-desktop`） |
| audience | 某个 API 的受众标识 | `docweave-api` |
| `workspace_id` | 租户；**它就是数据层的 tenant 值** | `w_3b6b2bb82321` |

一个应用可以有多个客户端，各自有独立的回调地址和密钥。ID Token 是给客户端验证身份用的，
**不是调用业务 API 的通用凭证**；API 只接受为自身受众签发、权限适当且当前有效的凭据。

---

## 1. 登记应用

```bash
python -m platform_identity.cli app list
```

DocWeave 在服务启动时自动登记（`platform_core/apps.py: bootstrap_docweave`），幂等。新增一个应用时
在同一位置登记，或扩展该函数。

工作空间要显式启用应用，成员要显式获得使用资格：

```bash
python -m platform_identity.cli app enable  docweave --workspace w_xxx
python -m platform_identity.cli app grant   docweave --workspace w_xxx --user someone@example.com
python -m platform_identity.cli app revoke  docweave --workspace w_xxx --user someone@example.com
python -m platform_identity.cli app disable docweave                      # 停用整个应用
```

新建的工作空间会在创建它的同一个事务里启用 `DEFAULT_WORKSPACE_APPS`（见
`apps/docweave/docweave/identity.py`）并给所有者授予资格。这不是顺手：`adopt_existing_members()` 是
一次性迁移，如果新空间不在创建时就配好，之后注册的每个账户都会登录成功、然后被告知什么都不能用。

workspace `default` 是历史数据空间，`legacy / pending_claim / 0 成员`。CLI 会拒绝直接对它授权，
必须走认领流程。

---

## 2. 登记 OIDC 客户端

```bash
python -m platform_identity.cli client register \
    --client-id my-app-web \
    --app my-app \
    --name 我的应用 \
    --redirect     http://myapp.localhost:8780/auth/callback \
    --post-logout  http://myapp.localhost:8780/signed-out \
    --backchannel  http://myapp.localhost:8780/auth/backchannel-logout
```

加 `--confidential` 会生成一次性 `client_secret`（只显示一次，库里只存哈希）。浏览器端应用用公共
客户端 + PKCE(S256)，不要把密钥放进前端包。

规则：

- **回调地址精确匹配**，没有通配符，没有前缀匹配，没有「同源就算」。
- 非本机地址必须 https；`http` 只允许 `localhost` / `127.0.0.1` / `*.localhost`。
- 没有动态客户端注册端点，将来也不会有。

```bash
python -m platform_identity.cli client list
python -m platform_identity.cli client show    --client-id my-app-web   # 不打印密钥，连哈希也不打印
python -m platform_identity.cli client disable --client-id my-app-web
```

---

## 3. 应用侧要实现的四个端点

`apps/docweave/docweave/rp.py` 和 `apps/docweave/docweave/api/rp_routes.py` 是一份可以照抄的参考实现。

| 路由 | 作用 | 关键约束 |
|---|---|---|
| `GET /auth/start` | 发起登录 | `state`/`nonce`/PKCE verifier 存服务端，浏览器只拿一个不透明 id |
| `GET /auth/callback` | 换令牌、建会话 | 服务端换码；令牌不进浏览器；落地地址只能是本站路径 |
| `POST /auth/backchannel-logout` | 接收登出通知 | 验签、验 iss/aud、拒绝带 `nonce` 的令牌、按 `jti` 去重 |
| `POST /api/auth/sign-out` | 退出本应用 | 与「退出平台」分开，文案要说清影响范围 |

四件容易做错的事：

1. **不要把访问令牌或刷新令牌放进 `localStorage`。** 用服务端会话（BFF）。页面能读到的令牌，
   注入的脚本也能读到。
2. **不要用 ID Token 当 API 凭证。** 它只在回调里验一次，验完就丢。
3. **`returnTo` 必须服务端约束。** 参考 `rp.py: safe_next()`：只接受本站路径，`//host`、
   `/\host`、带 scheme 的一律落到安全页。
4. **不要让应用继承平台管理员权限。** 登录只确认身份；业务操作仍要查账户状态、成员关系、应用资格、
   操作权限和对象归属。

### 连接身份服务的 HTTP 客户端

`rp.py` 里的客户端设了 `trust_env=False`：

> httpx 默认会遵循环境里的 `HTTP_PROXY`，而这个连接上跑的是授权码和（机密客户端的）客户端密钥。
> `NO_PROXY=localhost,127.0.0.1` **不匹配** `accounts.localhost`，所以这件事很容易在无人察觉时
> 发生——本机验证时就发生了：所有浏览器跳转都正常，只有服务端换码那一步被悄悄送进代理并被丢弃，
> 表现为回调处的 `Server disconnected without sending a response`。

确实需要经代理访问发行者的部署，用 `DOCWEAVE_OIDC_PROXY` 显式指定。

---

### 依赖方不要有自己的口令框

`/api/auth/state` 里的 `password_login` 由角色决定：只有 `accounts` 和 `all` 为真。依赖方
（`app` / `portal`）**根本不提供** `/api/auth/login`，因此它的 `RequireSession` 和 `/login`
都直接前往 `/auth/start`，由账户服务完成认证。

这条是浏览器验证时发现的：点门户上的 DocWeave 卡片，落到了 DocWeave 自己的登录表单，要求
再输一次口令——而那个表单永远不可能成功。它重要的地方不只是坏了：一个收集口令却无法校验口令
的表单，恰恰出现在「口令只在一个地方输入」这条规则最该被贯彻的页面上。

### 每个角色服务自己的前端外壳

一个 Vite 构建，三个入口：`index.html`（DocWeave 工作台）、`portal.html`（平台门户）、
`accounts.html`（账户与安全）。由 `DOCWEAVE_ROLE` 决定服务哪一个。

账户服务原本和 DocWeave 共用同一个外壳，于是它渲染出 DocWeave 的项目列表，然后被自己的角色
边界拒绝，页面上浮出「该接口不在账户服务上」——一次正确的拒绝被呈现成一个坏掉的页面。

新增应用时同理：给它自己的入口，不要把它塞进别人的 SPA 路由。

## 4. 本机跑起来并量一遍

```bash
python scripts/platform.py up          # 三个来源，一个数据库
python scripts/platform.py status
python tests/integration/verify_sso.py --email you@example.test --password '...'
python scripts/platform.py down
```

端口可用 `PLATFORM_ACCOUNTS_PORT` / `PLATFORM_PORTAL_PORT` / `PLATFORM_DOCWEAVE_PORT` 覆盖。

### 为什么是三个主机名而不是三个端口

**不同端口不隔离 Cookie。** Cookie 协议按主机划分作用域，完全忽略端口，所以
`localhost:8770` 设的 Cookie 会照样发给 `localhost:8765`。全用 `localhost` 换端口做出来的
「跨来源单点登录」，其实是一个共享 Cookie 戴了三顶帽子。

`*.localhost` 按 RFC 6761 全部解析到回环地址，不需要改 hosts 文件，就能得到真正分开的 Cookie
容器。`verify_sso.py` 用的是 Python 的 `http.cookiejar`——真实的、按主机作用域且无视端口的规则，
所以它对隔离的判断，换成浏览器也成立。

### 服务要监听自己域名解析到的地址

`*.localhost` 在本机同时解析到 `::1` 和 `127.0.0.1`，`getaddrinfo` 先给 `::1`。uvicorn 只绑一个
地址，绑 `127.0.0.1` 会让 `[::1]:8770` 拒绝连接——浏览器和 urllib 会挨个地址重试所以看不出问题，
httpx 不会，于是服务端换码那一步失败。`scripts/platform.py: bind_addr()` 因此按域名解析结果绑定。
生产环境同理：发行者域名和监听地址必须对得上。

---

## 5. 退出与撤权

三个入口意思不同，不要混：

| 操作 | 影响 | 入口 |
|---|---|---|
| 退出当前应用 | 只结束这个应用的会话 | `POST /api/auth/sign-out` |
| 退出平台 | 结束中央会话 + 所有已关联应用会话 | `GET {issuer}/oidc/logout` |
| 撤销全部设备 | 该用户的所有会话 | 账户安全页 |

后通道登出的实现要点，都在 `platform/routes.py: _end_central_session`：

- **先在自己的数据库里撤销，再通知。** 通知是让应用「及时」知道，数据库才是「正确」的来源。
  收不到通知的应用，下一次校验令牌照样失败。把通知当成执行机制，「到处退出」就会变成
  「在碰巧应答了的应用上退出」。
- 投递失败记录在 `client_sessions.logout_error`，不吞掉。
- 重复通知是成功且无事可做，不是错误——否则提供方会一直重试。
- `post_logout_redirect_uri` 对该客户端登记的列表精确匹配；未登记的直接 400，不跳转也不回显，
  **但会话照样结束**：拒绝跳转不能变成让人还留在登录状态。

刷新令牌轮换并带重放检测：每个刷新令牌只能用一次，用过的再出现就说明它泄露了（正当持有者手里
已经是下一个），于是整条令牌家族一起撤销。每次刷新都会重新检查账户状态和应用资格——这才是短期
访问令牌值钱的地方。

---

## 5.1 平台管理与历史数据认领

管理后台在账户服务上（`/admin`，`role=accounts`），业务角色访问返回 404。路由本身校验权限：
非管理员得到的是 404 而不是 403，因为一个普通账户不该从这里学到任何东西的存在。

```bash
# 认领只能在界面里做——它需要重认证、理由和事务，不是一条可以脚本化的命令。
# 但可以先看清楚将要发生什么：
python python -m docweave.cli --data data app list
```

认领的约束写在 `docs/security/permissions.md` §3.3。要点：

* 目标必须是**已存在**的账户；
* 需要 `scope=admin` 且 **strict** 的近期重认证——刚登录不算，必须为这件事重新输入口令；
* 需要书面理由，原样进审计；
* 一个事务内完成成员关系、空间状态、应用启用与资格；
* **不改写任何一行业务数据**；
* 不给管理员自己任何读取权。

重认证作用域是一个封闭集合（`mfa` / `admin`）。空作用域满足所有检查，所以它必须是请求无法要求的值。

## 5.2 机器身份：服务账户与用户委托

两种机器调用者，区别很重要：

| | 服务账户 | 用户委托 |
|---|---|---|
| 背后有人吗 | 没有，它以自己的身份行动 | 有，它代表授权它的那个人 |
| 租户从哪来 | 登记时绑定的工作空间 | 每次调用重新按成员关系解析 |
| 权限 | 登记时的 `api_scopes` ∩ 本次申请 | 客户端许可 ∩ **当前**角色 ∩ 本次授权 |
| 生命周期 | 独立；有人退出浏览器不影响它 | 随账户状态、成员关系、应用资格一起失效 |
| 流程 | `client_credentials` | 授权码流程签发的访问令牌 |

登记一个服务账户：

```bash
python -m platform_identity.cli client register \
    --client-id nightly-batch --app docweave --name 夜间批处理 \
    --service --workspace w_xxx --api-scopes "docweave.read docweave.write"
```

必须是机密客户端（密钥就是它的全部凭据），必须绑定工作空间（背后没有人，租户推不出来）。
`client_secret` 只显示一次。

取令牌并调用（`tests/integration/automation_example.py` 原样支持）：

```bash
python tests/integration/automation_example.py \
    --base-url http://docweave.localhost:8775 \
    --issuer http://accounts.localhost:8770 \
    --client-id nightly-batch --client-secret "$SECRET" \
    --source testdata/测试_客户100.xlsx --template testdata/测试_合同模板.docx \
    --definition docs/integration/example-definition.json --out ./out
```

**本机实跑通过**：取令牌 → 建项目 → 上传来源与模板 → 写映射 → 预检 → 提交任务 →
真实状态事件 → 下载 10 个 .docx（每个约 39 KB，内容含真实客户数据）。全程没有浏览器、
没有 Cookie、没有点击。

### 每次调用都重新核验，不看令牌当初记了什么

`apps/docweave/docweave/machine.py` 在**每一次**请求上重新问三个问题：账户还在不在、这个人还是不是
该空间成员、该空间还有没有这个应用。令牌里记的是当初授予了什么，不是现在还成不成立。把这些
固化进令牌，就是「已被撤权的人继续工作到令牌过期」的成因。

角色还会**收窄**委托：令牌签发时是 editor、现在被改成 viewer，那么读仍然可以，写会被拒——
令牌没有被撤销，但角色说了算。

### ID Token 不是 API 凭证

只带 `openid profile email` 的访问令牌调用 `/api/v1` 会被 403 拒绝，并明确告诉你需要申请带
`docweave.*` 权限的令牌。它证明的是谁登录了，不是可以运行什么。

### 旧的 `dw_` 密钥继续有效

它们早于平台存在，一次性全部作废会打断正在运行的自动化。两种凭据走同一个解析函数，出来的
principal 形状相同，因此没有任何调用点需要知道自己拿到的是哪一种。

## 6. 密钥

```bash
python -m platform_identity.cli keys list
python -m platform_identity.cli keys rotate            # 新密钥签名，旧密钥转 retiring 仍在 JWKS 中
python -m platform_identity.cli keys retire --kid k_xxx   # 等最长令牌有效期过去之后
```

签名密钥持久化保存。每次启动重新生成密钥，会让所有已签发令牌失效——那看起来就是一次没有原因的
集体登出。

---

## 6.1 找回口令与邮箱验证

```bash
# 本机用投递目录，不发真信
export DOCWEAVE_MAIL_MODE=file
export DOCWEAVE_MAIL_DIR=./data/mail
export DOCWEAVE_PUBLIC_URL=http://accounts.localhost:8770
```

`DOCWEAVE_PUBLIC_URL` 是必须的：邮件里的链接不能从请求的 Host 推导，否则目标地址由攻击者控制
而令牌在真实主机上仍然有效。

流程的三条规则写在 `docs/security/permissions.md` §3.6，最容易做错的两条：

1. **请求重置对任何地址（以及邮件发送失败时）都给同一个答复。** 否则这就是一个无需认证的账户
   存在性探测器。
2. **链接不是动作。** 打开链接只读取令牌，消费要第二次明确的 POST——邮件安全扫描器会跟随链接。

**本地投递目录是测试设施，不是生产邮件可送达的证据。** 真实投递、域名与邮件信誉属独立外部条件。

## 7. 还没有做的

按「本轮已验证完成 / 只在合成样本验证 / 仍需外部条件」如实记录，见
`docs/quality/progress.md`。与接入相关的未完成项：

- Passkey / WebAuthn：RP ID 与可信 origin 策略已确定为账户服务域名，实现尚未落地。
- 机器身份（服务账户、用户委托）仍走现有 `/api/v1` 密钥体系，尚未接入 OIDC 的客户端凭据流程。
- 管理后台与历史数据认领界面未开始。门户首页、账户与安全页已完成并在浏览器中走通。

---

## 10. 一个新应用放在仓库的哪里

平台是单仓库，新应用有它自己的目录，和 DocWeave 平级：

```
apps/<your-app>/
├─ pyproject.toml        声明依赖：platform-core、platform-auth-client，**不写任何别的应用**
├─ requirements.txt      -r ../../packages/auth-client/python/requirements.txt + 自己的
├─ <your_app>/           Python 包：app.py（工厂）、routes.py、业务实现
├─ web/                  自己的 Vite 构建；package.json 依赖 @platform/ui、@platform/auth-client
│                        build.assetsDir 取一个**不重名**的目录名，否则单进程部署里会互相遮盖
├─ tests/                这个应用自己的测试
└─ AGENTS.md             这个模块的工作边界
```

接入统一身份**不需要修改 DocWeave 的任何代码**，也不需要改账户服务的实现。要做的是四件事：

1. **挂上依赖方路由**（授权码流程、回调、后通道登出，都在共享包里）：

   ```python
   from platform_auth_client.routes import build_router as build_rp_router
   from platform_auth_client.session_routes import build_router as build_session_router
   from platform_core.http import build_platform, install as install_platform, new_api

   def create_app(data_dir=None):
       plat = build_platform(data_dir)
       api = new_api("Your App")
       install_platform(api, plat, role="app")
       api.include_router(build_session_router(plat.identity, plat.sessions, plat.audit))
       api.include_router(build_rp_router(plat.store, plat.identity, plat.sessions, plat.audit))
       ...
   ```

   不要自己写授权码流程，也不要给应用加第二个口令框——`role != accounts` 的进程根本不提供
   `/api/auth/login`，那个框永远不可能成功。

2. **登记应用与客户端**（§1、§2），并在 `platform_core/apps.py` 里登记 `app_id`。

3. **前端用共享包**：`@platform/ui` 拿设计变量与基础组件，`@platform/auth-client` 拿
   `SessionProvider` / `RequireSession` / `signInUrl`。账户与安全页面**不要复制**：链接到
   `state.account_url`。

4. **把它加进组装层**：`infra/server/platform_server/app.py` 按角色挂载。这是唯一允许同时导入
   多个应用的地方。

然后 `python scripts/check_boundaries.py` 必须通过——它会告诉你有没有不小心伸进别人的实现里。

### 自动化编排类产品怎么接

不需要改 DocWeave。它已经有完整的机器接口（`/api/v1`，见
[api.md](api.md)）与机器身份（§5 的服务账户与用户委托）：取令牌 → 提交任务 → 查询 → 取回产物，
全程无浏览器、无 Cookie，权限精确到 app / workspace / 操作。编排产品作为**另一个应用**接入统一
身份，用自己的服务账户调用 DocWeave 的公开接口，而不是 import 它的 Python 包。
