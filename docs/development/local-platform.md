# 从干净环境启动本机平台

从**这个仓库的根目录**启动整套平台：账户服务、平台门户、DocWeave。不需要另一个仓库，不需要
容器编排，不需要改 `/etc/hosts`。

**全部在本机。** 下面没有任何一条命令会部署、修改 DNS、建隧道或对外暴露服务。

---

## 0. 前置

| 依赖 | 版本 / 来源 | 必需？ |
|---|---|---|
| Python | 3.14.5（本机实测版本；容器里是 3.12） | 必需 |
| Node.js + npm | 用于构建前端 | 必需 |
| LibreOffice | 旧格式转换、PDF 导出、真实分页 | 可选，缺了会降级 |
| Tesseract + `chi_sim`/`eng` | 扫描件 OCR | 可选 |
| 数据库 | **无**。SQLite 在 `data/` 里，不需要单独的数据库服务 | — |
| 消息队列 | **无**。任务由进程内后台线程执行 | — |
| 本地邮件接收器 | **无需额外软件**，见 §5 | — |

一次性安装：

```bash
scripts/setup.sh
```

它建立 `backend/.venv`、安装 `backend/requirements.txt`、构建前端、下载 OCR 语言数据。

---

## 1. 三个来源，一条命令

```bash
python scripts/platform_local.py up
```

| 服务 | 角色 | 地址 | 服务的前端 |
|---|---|---|---|
| 账户服务 | `accounts` | http://accounts.localhost:8770 | `accounts.html`（登录、账户与安全、管理后台） |
| 平台门户 | `portal` | http://portal.localhost:8769 | `portal.html`（平台首页） |
| DocWeave | `app` | http://docweave.localhost:8765 | `index.html`（文档工作台） |

端口可覆盖（开发机上 8765 经常已被占用）：

```bash
PLATFORM_DOCWEAVE_PORT=8775 python scripts/platform_local.py up
```

其它命令：

```bash
python scripts/platform_local.py status                 # 进程、端口、发行者、支持的 scope
python scripts/platform_local.py logs accounts -n 200   # 也可以是 portal / docweave
python scripts/platform_local.py down
```

日志与 pid 在 `.local/platform/`（已加入 `.gitignore`）。

### 1.1 为什么是三个主机名，不是三个端口

**不同端口不隔离 Cookie。** Cookie 协议按主机划分作用域并完全忽略端口，所以 `localhost:8770`
设的 Cookie 会照样发给 `localhost:8765`。全用 `localhost` 换端口做出来的"跨来源单点登录"，其实
是一个共享 Cookie 戴了三顶帽子。

`*.localhost` 按 RFC 6761 全部解析到回环地址，**不需要改 hosts 文件**。`platform_local.py up`
会先检查这三个名字确实解析到回环地址，不通过就中止。

### 1.2 服务监听自己域名解析到的地址

`*.localhost` 在本机同时解析到 `::1` 和 `127.0.0.1`，`getaddrinfo` 先返回 `::1`。uvicorn 只绑一个
地址；绑 `127.0.0.1` 会让 `[::1]:8770` 拒绝连接。浏览器和 urllib 会挨个地址重试所以看不出问题，
**httpx 不会**——于是只有服务端换码那一步失败。`bind_addr()` 因此按域名解析结果绑定。

**监听范围**：始终是回环地址。`backend/run.py` 的 `check_listen_config()` 会在
`DOCWEAVE_HOST` 对外监听且 `DOCWEAVE_UI_AUTH=none` 时**拒绝启动**，不会退回无认证。

---

## 2. 数据在哪里

**默认原地不动**：`data/`（`docweave.sqlite3` + `blobs/`）。

`platform_local.py` 把 `DOCWEAVE_DATA_DIR` 显式传给三个子进程，所以**不会**在别的目录里建出
一个"空数据库"被误认为用户数据丢失。三个进程**共用同一个数据库**——必须只有一个账户权威来源。

用隔离副本试（推荐，不动真实数据）：

```bash
DOCWEAVE_DATA_DIR=/tmp/platform-demo python scripts/platform_local.py up
```

迁移由任何一个打开数据库的进程执行，幂等，纯追加。详见
[identity-migration.md](../security/identity-migration.md)。

---

## 3. 第一个账户

没有默认管理员，没有默认口令。

```bash
python backend/manage.py --data data bootstrap admin \
    --email you@example.test --name 你的名字
# 不带 --password 会交互式询问；口令策略是真的，弱口令会被拒绝
```

或者生成一次性引导令牌，在浏览器里完成：

```bash
python backend/manage.py --data data bootstrap token
```

> 建立平台管理员**不会**让你获得历史数据。`data/` 里的 68 个历史项目在 workspace `default`
> 里，`legacy / pending_claim / 0 成员`。要取得它们，到 http://accounts.localhost:8770/admin
> 走认领流程（需要重新输口令 + 书面理由，全程审计）。

---

## 4. 登记应用与客户端

`platform_local.py up` 已经自动登记了门户和 DocWeave 两个客户端。手动做的话：

```bash
python backend/manage.py --data data client register \
    --client-id docweave-web --app docweave --name DocWeave \
    --redirect    http://docweave.localhost:8765/auth/callback \
    --post-logout http://docweave.localhost:8765/signed-out \
    --backchannel http://docweave.localhost:8765/auth/backchannel-logout
```

机器身份（服务账户）：

```bash
python backend/manage.py --data data client register \
    --client-id nightly-batch --app docweave --name 夜间批处理 \
    --service --workspace w_xxx --api-scopes "docweave.read docweave.write"
# client_secret 只显示一次。写进运行它的东西的环境变量，不要提交进仓库。
```

完整说明见 [app-onboarding.md](../integration/app-onboarding.md)。

---

## 5. 本地邮件

**不需要额外软件。** 邮件有三种模式，由 `DOCWEAVE_MAIL_MODE` 决定：

| 模式 | 行为 |
|---|---|
| `disabled` | 默认（未设 `DOCWEAVE_SMTP_HOST` 时）。发送会明确失败，不静默丢弃 |
| `file` | **本机开发用**。排队并写入 `DOCWEAVE_MAIL_DIR` 目录 |
| `smtp` | 真实发送，需要 `DOCWEAVE_SMTP_HOST` 与 `DOCWEAVE_PUBLIC_URL` |

```bash
export DOCWEAVE_MAIL_MODE=file
export DOCWEAVE_MAIL_DIR=./data/mail
export DOCWEAVE_PUBLIC_URL=http://accounts.localhost:8770
```

`DOCWEAVE_PUBLIC_URL` 是必需的：邮件里的链接**不能从请求的 Host 推导**，否则目标地址由攻击者
控制而令牌在真实主机上仍然有效。

> **投递目录是测试设施，不是生产可送达的证据。** 真实投递、域名与邮件信誉是独立的外部条件。
> 收件箱里出现了文件，只说明代码把信写出来了。

---

## 6. 证书与 Passkey

本机走**明文 HTTP**，不需要任何证书，也不需要改浏览器信任设置。

这可行是因为浏览器把 `*.localhost` 当作**安全上下文**（`window.isSecureContext === true`），
所以 WebAuthn 在这些主机名上可用——本机实测 `isUserVerifyingPlatformAuthenticatorAvailable()`
返回 `true`。

对应地，本机必须 `DOCWEAVE_COOKIE_SECURE=0`（`platform_local.py` 已设），否则 Cookie 在明文
HTTP 上根本不会被写入。

**这也是这些主机名绝不能用于任何真实用途的原因。**

Passkey 的 RP ID 与可信 origin：

```bash
export DOCWEAVE_RP_ID=accounts.localhost              # 不设则取发行者域名
export DOCWEAVE_RP_ORIGINS=http://accounts.localhost:8770   # 额外可信 origin，逗号分隔
```

> **改域名不会迁移 Passkey。** 凭据绑定在注册时的域名上，换成生产域名后它们在新域名下根本
> 不存在，必须重新登记。界面会把这种凭据显示为"域名已变更，无法使用"，而不是让它悄悄消失。
> 绝不通过关闭 origin 校验或全局关闭证书验证来"凑通"。

---

## 7. 只启动 DocWeave（不要平台）

DocWeave 保留独立启动方式，这是"独立应用"的一部分：

```bash
scripts/start.sh            # 或 backend/.venv/bin/python backend/run.py
```

单进程形态下 `DOCWEAVE_ROLE` 未设 ⇒ 视为 `all`：所有路由挂在一个进程上，服务 `index.html`，
认证模式由 `DOCWEAVE_UI_AUTH` 决定（默认 `none`）。这就是升级前的行为，没有变化。

`PlatformNav`（返回平台 / 工作空间切换 / 账户）在 `all` 模式下**不渲染**——单进程部署没有门户
可以返回，一个指向不存在页面的"返回平台"比没有这个链接更糟。

---

## 8. 验证它真的通了

```bash
# 三条登录链路 + Cookie 主机隔离（用真实 http.cookiejar 量，不是按端口假设）
python scripts/verify_sso.py --email you@example.test --password '...'

# 真实数据库的隔离副本上跑迁移，逐行比对
python scripts/migration_rehearsal.py --from data

# 自动化链路：无浏览器、无 Cookie，取令牌 → 提交任务 → 取回真实产物
python scripts/automation_example.py \
    --base-url http://docweave.localhost:8765 \
    --issuer   http://accounts.localhost:8770 \
    --client-id nightly-batch --client-secret "$SECRET" \
    --source testdata/测试_客户100.xlsx \
    --template testdata/测试_合同模板.docx \
    --definition docs/integration/example-definition.json --out ./out

# 后端测试
cd backend && .venv/bin/python -m pytest -q

# 前端类型检查与构建
cd frontend && npx tsc --noEmit && npm run build
```

---

## 9. 环境变量速查

只列平台相关的；DocWeave 自身的见 [../DEPLOY.md](../DEPLOY.md)。

| 变量 | 默认 | 作用 |
|---|---|---|
| `DOCWEAVE_ROLE` | `all` | `accounts` / `portal` / `app` / `all` |
| `DOCWEAVE_DATA_DIR` | `./data` | 数据库与 blob 目录 |
| `DOCWEAVE_UI_AUTH` | `none` | `none` / `basic` / `proxy` / `session` |
| `DOCWEAVE_ISSUER` | `http://accounts.localhost:8770` | 账户服务地址；**配置而来，绝不从请求推导** |
| `DOCWEAVE_APP_URL` | 按角色 | **本服务自己**的对外地址；必须与登记的回调完全一致 |
| `DOCWEAVE_PORTAL_URL` | 空 | 账户页"返回"的目标；空则不显示该链接 |
| `DOCWEAVE_OIDC_CLIENT_ID` | `docweave-web` | 本服务作为客户端的 id |
| `DOCWEAVE_OIDC_APP_ID` | `docweave` | 本服务是哪个应用（门户是 `platform`） |
| `DOCWEAVE_OIDC_PROXY` | 空 | 访问发行者需要经过的代理。**不继承 `HTTP_PROXY`**，见下 |
| `DOCWEAVE_PLATFORM_NAME` | `工作平台` | 平台显示名；**不要写死成 DocWeave** |
| `DOCWEAVE_RP_ID` | 发行者域名 | Passkey 绑定的域名 |
| `DOCWEAVE_RP_ORIGINS` | 空 | 额外可信 origin，逗号分隔 |
| `DOCWEAVE_COOKIE_SECURE` | `1` | 本机明文 HTTP 必须设 `0` |
| `DOCWEAVE_SECRET` | 空 | MFA 种子的加密密钥。**不设则退回库内 `meta.server_secret`**，等于密钥与密文同库 |
| `DOCWEAVE_SESSION_IDLE_S` | 43200 | 会话空闲过期 |
| `DOCWEAVE_SESSION_ABSOLUTE_S` | 2592000 | 会话绝对过期 |
| `DOCWEAVE_ADMIN_IDLE_S` | 3600 | 平台管理员的空闲过期 |
| `DOCWEAVE_REAUTH_S` | 600 | 重认证有效期 |
| `DOCWEAVE_ACCESS_TTL_S` | 900 | 访问令牌有效期 |
| `DOCWEAVE_REFRESH_TTL_S` | 1209600 | 刷新令牌有效期 |
| `DOCWEAVE_MAIL_MODE` | `disabled` | `disabled` / `file` / `smtp` |
| `DOCWEAVE_MAIL_DIR` | `./data/mail` | `file` 模式的投递目录 |
| `DOCWEAVE_PUBLIC_URL` | 空 | 邮件里链接的来源；发信前必须设置 |

> **`DOCWEAVE_OIDC_PROXY` 而不是 `HTTP_PROXY`**：httpx 默认遵循环境里的代理设置，而与身份服务
> 的连接上跑的是授权码和客户端密钥。`NO_PROXY=localhost,127.0.0.1` **不匹配**
> `accounts.localhost`，所以这件事很容易在无人察觉时发生——本机验证时就发生过。因此那几个
> 客户端都设了 `trust_env=False`，需要代理必须显式指名。

---

## 10. 常见问题

**端口被占用** — 很可能是你自己在终端里起过一个 DocWeave。先确认再决定：

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
```

如果就是这个项目的 `backend/run.py`，不要再起一个（两个进程共用一个 SQLite 库），
要么直接用它，要么停掉它再 `up`。也可以用 `PLATFORM_DOCWEAVE_PORT` 换个端口。

**回调处 `Server disconnected without sending a response`** — 见 §9 的代理说明。

**浏览器跳转都正常，只有换码失败** — 同上，或者服务绑错了地址族（§1.2）。

**登录后什么都打不开** — 工作空间没有启用应用。`python backend/manage.py --data data app list`
看一眼，用 `app enable docweave --workspace w_xxx` 修。新建的工作空间会在创建的同一个事务里自动
配好，只有历史数据空间需要走认领。

**Passkey 按钮点了没反应** — 检查 `window.isSecureContext`。用 `127.0.0.1:8770` 而不是
`accounts.localhost:8770` 访问会丢掉安全上下文。
