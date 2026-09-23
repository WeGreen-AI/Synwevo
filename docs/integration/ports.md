# 端口与监听（实测）

本文件的端口、绑定地址与健康检查都来自当前代码与实际运行验证（命令见文末「如何自行核对」）。
**DocWeave 是单进程服务**：Web 界面、机器接口、任务运行线程、Webhook 投递线程都在同一个进程内；数据库是本地 SQLite 文件，没有独立端口。

## 1. 监听端口一览

| 服务 / 进程 | 用途 | 协议 | 默认端口 | 默认绑定 | 配置项 | 是否对外 | 鉴权 |
|---|---|---|---|---|---|---|---|
| `backend/run.py`（uvicorn，单进程） | Web 界面 + 机器接口 + 健康检查 | HTTP/1.1 | **8765** | `127.0.0.1` | `DOCWEAVE_PORT` / `DOCWEAVE_HOST` | 建议仅经反向代理对外 | `/api/v1/*` 必须 API 密钥；界面按 `DOCWEAVE_UI_AUTH` |
| Vite 开发服务器（仅开发） | 前端热更新，`/api` 反向代理到 8765 | HTTP/1.1 | **5173** | `localhost` | `frontend/vite.config.ts` 的 `server.port` | 否（仅本机开发） | 无（代理到后端后按后端规则） |
| 任务运行线程 `job-runner` / 看门狗 `job-supervisor` | 执行生成任务、租约心跳、恢复 | — | **无监听端口** | — | `DOCWEAVE_JOB_LEASE_S` | — | — |
| Webhook 投递线程 `webhooks` | 主动向外发起 HTTPS 回调 | 出站 HTTP(S) | **无监听端口** | — | `DOCWEAVE_WEBHOOK_*` | 出站 | 请求带 HMAC 签名 |
| SQLite（`data/docweave.sqlite3`） | 元数据、任务、事件 | 文件 | **无监听端口** | — | `DOCWEAVE_DATA_DIR` | 否 | 文件系统权限 |
| 内容寻址文件存储（`data/blobs/`） | 来源文件、模板版本、输出产物 | 文件 | **无监听端口** | — | `DOCWEAVE_DATA_DIR` | 否 | 文件系统权限 |
| LibreOffice（`soffice --headless`） | 旧格式转换、PDF 导出、分页计算 | 子进程 | **无监听端口**（使用私有配置目录，不启动监听） | — | `DOCWEAVE_SOFFICE`、`DOCWEAVE_LO_PROFILE` | 否 | — |
| Tesseract（`tesseract`） | 扫描件 / 图片 OCR | 子进程 | **无监听端口** | — | `DOCWEAVE_TESSERACT`、`DOCWEAVE_TESSDATA` | 否 | — |

没有独立的队列服务、对象存储服务或后台管理端口；`docker-compose.yml` 中也只映射 8765。

## 1.1 平台模式：一个二进制，三个角色，三个来源

上面那张表描述的是**单进程形态**（`DOCWEAVE_ROLE` 未设 ⇒ `all`），也是 `dw.wegreen.ltd` 今天
运行的形态。启用平台模式后，同一个二进制以三个角色各起一个进程：

| 角色 | 用途 | 默认端口 | 默认绑定 | 服务的前端 | 鉴权 |
|---|---|---|---|---|---|
| `accounts` | 账户服务 / OIDC Provider、账户与安全、管理后台 | **8770**（`PLATFORM_ACCOUNTS_PORT`） | 见下 | `accounts.html` | `/api/auth/*` 会话；`/oidc/*` 各自携带自己的认证 |
| `portal` | 平台门户（接入统一身份的客户端） | **8769**（`PLATFORM_PORTAL_PORT`） | 见下 | `portal.html` | 会话 |
| `app` | DocWeave 业务应用 | **8765**（`PLATFORM_DOCWEAVE_PORT`） | 见下 | `index.html` | 会话；`/api/v1/*` 机器凭据 |

本机地址分别是 `http://accounts.localhost:8770`、`http://portal.localhost:8769`、
`http://docweave.localhost:8765`。

> **为什么是三个主机名而不是三个端口：不同端口不隔离 Cookie。** Cookie 协议按主机划分作用域并
> 完全忽略端口，所以 `localhost:8770` 设的 Cookie 会照样发给 `localhost:8765`。全用 `localhost`
> 换端口做出来的"跨来源单点登录"，其实是一个共享 Cookie 戴了三顶帽子。`*.localhost` 按 RFC 6761
> 全部解析到回环地址，不需要改 hosts 文件。

**绑定地址按域名解析结果决定**，不是固定 `127.0.0.1`。`*.localhost` 在本机同时解析到 `::1` 和
`127.0.0.1` 且 `getaddrinfo` 先返回 `::1`；uvicorn 只绑一个地址，绑错会让浏览器正常而
服务端换码失败（httpx 不做地址回退）。`scripts/platform.py: bind_addr()` 负责这件事。

角色边界由网关中的一个过滤器强制执行，**不是靠"没挂载那个路由"**：

| 请求 | `accounts` | `portal` | `app` |
|---|---|---|---|
| `/oidc/*`、`/.well-known/*` | 200 | 404 | 404 |
| `/api/auth/*`（除会话自身的几条） | 200 | 404 | 404 |
| `/api/admin/*` | 200 | 404 | 404 |
| `/api/portal/*` | 404 | 200 | 404 |
| `/api/projects`、`/api/v1/*` | 404 | 404 | 200 |
| `/api/auth/state`、`/logout`、`/me`、`/workspace`、`/sign-out` | 200 | 200 | 200 |
| `/api/health`、`/healthz`、`/readyz`、`/assets/*` | 200 | 200 | 200 |

过滤器表达为"账户服务拥有的路径**减去**允许清单"，而不是一张要封的前缀名单——这样账户服务以后
新增的路由默认被拒绝，而不是悄悄可达。

### 1.2 协议端点（账户服务上）

| 路径 | 用途 | 鉴权 |
|---|---|---|
| `GET /.well-known/openid-configuration` | discovery | 无 |
| `GET /oidc/jwks.json` | 公钥 | 无 |
| `GET /oidc/authorize` | 授权端点 | 中央会话 Cookie（没有则跳登录） |
| `POST /oidc/token` | 令牌端点（`authorization_code` / `refresh_token` / `client_credentials`） | 客户端凭据；**完全不看 Cookie** |
| `GET /oidc/userinfo` | 用户信息 | Bearer 访问令牌 |
| `POST /oidc/revoke` | 撤销 | 客户端凭据 |
| `GET /oidc/logout` | RP 发起的登出 | 中央会话 Cookie |

### 1.3 依赖方端点（门户与 DocWeave 上）

| 路径 | 用途 | 鉴权 |
|---|---|---|
| `GET /auth/start` | 发起登录 | 无（这是入口） |
| `GET /auth/callback` | 换码建会话 | 服务端请求行 + `dw_authreq` Cookie |
| `POST /auth/backchannel-logout` | 接收登出通知 | **签名的登出令牌本身**；无 Cookie、无 CSRF |
| `POST /api/auth/sign-out` | 退出本应用 | 会话 + CSRF |

**这些协议与 SSO 端点不经过旧的 Basic 关卡**：它们各自携带自己的认证，而在一条重定向链中间
弹出 HTTP 认证对话框不是登录流程。

## 2. 地址与路径

| 用途 | 地址（默认部署） |
|---|---|
| Web 界面 | `http://127.0.0.1:8765/` |
| 机器接口根 | `http://127.0.0.1:8765/api/v1` |
| 存活检查 | `GET http://127.0.0.1:8765/healthz` → `{"status":"ok"}`（无需鉴权） |
| 就绪检查 | `GET http://127.0.0.1:8765/readyz` → 200 `{"status":"ready"}` / 503 `{"status":"not_ready"}`（无需鉴权，只返回组件级状态，不含敏感诊断） |
| 界面内部接口（非自动化契约，可能随界面调整） | `http://127.0.0.1:8765/api/...`（不含 `/v1`） |
| 交互式接口文档 | 默认**关闭**；`DOCWEAVE_API_DOCS=1` 时为 `/api/docs`、`/api/openapi.json`（仅建议在开发环境开启） |

## 3. 容器与反向代理

* 镜像内监听 `0.0.0.0:8765`（`Dockerfile` 中 `DOCWEAVE_HOST=0.0.0.0`、`EXPOSE 8765`）。
* `docker-compose.yml` 映射为 `127.0.0.1:8765:8765`：**只在宿主机本地可达**，对外请另配反向代理。
* 绑定非本机地址且 `DOCWEAVE_UI_AUTH=none` 时，`backend/run.py` **拒绝启动**并打印三种处理方式（实测：`DOCWEAVE_HOST=0.0.0.0 python -m platform_server.run` 退出码 1，输出「拒绝启动…」）。设为 `basic` 或 `proxy` 后正常启动。
* 容器是例外：镜像内必须绑定 `0.0.0.0`，实际暴露范围由端口映射决定，进程无法判断，因此容器内（`DOCWEAVE_IN_CONTAINER=1`，镜像已设置）**只打印警告**而不拒绝。`docker-compose.yml` 只映射到 `127.0.0.1:8765`，对外请在前面加带认证的反向代理。
* 机器接口 `/api/v1` 与此设置无关，任何情况下都需要 API 密钥。
* 反向代理示例（Nginx，界面走 SSO/Basic，机器接口走密钥）：

```nginx
# 这四项用默认值会直接坏事，别省：
client_max_body_size 100m;        # 默认 1m：超过 1MB 的来源文件全部上传失败
proxy_read_timeout   300s;        # 默认 60s：LibreOffice 转换 / OCR 会被切断
proxy_send_timeout   300s;
proxy_request_buffering off;      # 大文件边传边转，避免先落盘再转发

proxy_set_header Host              $host;        # 默认是 $proxy_host，会让下载链接变成 127.0.0.1
proxy_set_header X-Forwarded-Proto $scheme;      # 否则签发出来的下载链接是 http://
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;

location /api/v1/ { proxy_pass http://127.0.0.1:8765; }          # 机器接口：由 API 密钥鉴权
location / {                                                      # 界面：由代理认证
    auth_basic "DocWeave"; auth_basic_user_file /etc/nginx/docweave.htpasswd;
    proxy_pass http://127.0.0.1:8765;
}
location = /healthz { proxy_pass http://127.0.0.1:8765; access_log off; }
```

* 更省事的做法是直接设 `DOCWEAVE_PUBLIC_URL=https://你的域名`，下载链接就不再依赖任何转发头。
* `auth_basic`（代理认证）和 `DOCWEAVE_UI_AUTH=basic`（应用自带口令）是**两套独立凭据，二选一**，
  同时开会要求登录两次。
* `DOCWEAVE_MAX_FILE_MB` 应当设得低于代理的体积上限（Cloudflare 免费/专业版 100 MB），
  否则用户会先撞到边缘的 413，而不是 DocWeave 自己那句中文提示。

## 4. 端口冲突与变更

* 改端口：`DOCWEAVE_PORT=9000 python -m platform_server.run`（或 compose 中改映射）。
* 端口被占用时 uvicorn 会以 `[Errno 48] Address already in use` 退出；用 `lsof -nP -iTCP:8765 -sTCP:LISTEN` 查看占用进程。
* 开发时前端 5173 与后端 8765 同时运行；前端通过 `vite.config.ts` 的 proxy 访问后端，不需要额外端口。

## 5. 如何自行核对

```bash
python -m docweave.cli info                       # 数据目录、schema 版本、当前监听配置
lsof -nP -iTCP -sTCP:LISTEN | grep -E '8765|5173'   # 实际监听（macOS/Linux）
curl -s localhost:8765/healthz                      # 存活
curl -s localhost:8765/readyz                       # 就绪（数据库 / 存储 / 运行线程）
curl -s -o /dev/null -w '%{http_code}\n' localhost:8765/api/v1/capabilities   # 401：机器接口需要密钥
```

实测记录（2026-09-23，macOS 26.0 / Python 3.14 / 本机部署）：`lsof` 显示 `Python … TCP 127.0.0.1:8765 (LISTEN)`；
`/healthz` → `{"status":"ok","version":"1.0.0"}`；`/readyz` → `{"status":"ready","checks":{"database":"ok","storage":"ok","worker":"ok"},"schema_version":3}`；
无密钥访问 `/api/v1/capabilities` → `401`。

---

## 6. 放在 WAF / CDN 之后：页面能打开 ≠ 接口能用

> 本节早先的版本写着「真人浏览器可以通过」「仅做浏览器访问则无需改动」。**这是错的**，
> 2026-09-23 在 `dw.wegreen.ltd` 上被实际推翻：用户在已通过校验的浏览器里打开项目、点开
> 「定义样本」，「识别结果预览」直接报错。页面能打开完全不代表界面能用。

### 6.1 失效模式

Cloudflare 的 Bot Fight / 托管质询是**靠返回一个质询页让浏览器去执行**来放行的。
整页导航可以这样过；但页面里的 `fetch()` / `XHR` 不行——质询页没法在一个 XHR 里渲染，
浏览器只拿到一个 `403`。于是：

* 首页、项目页都能打开（导航请求过了质询）；
* 点任何一个真正干活的按钮就报错（该动作的 XHR 被边缘挡掉）。

受影响的不是「程序化访问」，而是**任何拿不到交互式质询页的请求**——浏览器里的 XHR 正是这一类。
上传（multipart POST）通常最先中招。

### 6.2 先判断这个 403 是谁返回的

DocWeave 的每个响应都带 `X-Request-ID`，每个错误都是 JSON；边缘的质询页两样都没有。
界面从 v1.0 起据此区分，直接显示「请求被 Cloudflare 拦截（HTTP 403），没有到达 DocWeave」，
不再把中间层的拒绝说成是本服务拒绝的。人工核对：

```bash
# 服务端访问日志里有没有这条路径？没有 = 请求根本没到达服务，问题在边缘
docker logs docweave 2>&1 | grep rule-preview

# 直连源站复现同一个请求；这里成功就说明应用侧没问题
curl -s -u <用户名>:<口令> -X POST http://127.0.0.1:8765/api/projects/<pid>/rule-preview \
  -H 'Content-Type: application/json' -d '{"mode":"per_file"}'
```

### 6.3 要在 Cloudflare 面板改什么

面板位置随 Cloudflare 改版而变，以下按功能描述，**请以面板当时的实际位置为准**：

* **Bot Fight Mode**（安全 → 机器人）对该域名关闭。它不支持按主机名豁免，是整域开关。
* 或在 **WAF → 自定义规则**里加一条 `Skip` 规则：匹配 `Hostname equals dw.wegreen.ltd`
  （要更紧可再加 `URI Path starts with /api/`），动作选 Skip，跳过托管规则与机器人防护。
* 若还启用了**速率限制**，注意本界面在任务运行和文件解析期间是 900ms 轮询，容易触发。

### 6.4 关掉边缘防护之后还剩什么防护

DocWeave 自己有口令（`DOCWEAVE_UI_AUTH=basic`，PBKDF2 存储），`/api/v1` 另外要 API 密钥，
源站只监听回环、没有对外端口。所以为这个主机名跳过边缘防护**不等于裸奔**。
真正失去的是：针对该主机名的自动化扫描/撞库拦截、DDoS 之前的机器人过滤，以及托管规则对
已知漏洞特征的拦截——因此 HTTP Basic 的口令强度和及时升级就更重要了。

## 生产部署实例：vultr_tokyo（2026-09-23）

| 项 | 值 |
|---|---|
| 主机 | `vultr_tokyo`（Ubuntu 24.04，2 核 / 3.9 GB 内存 / 8 GB swap） |
| 运行方式 | Docker 容器 `docweave`，镜像 `docweave:local`（1.32 GB，含 LibreOffice 7.4.7.2、Tesseract 5.3.0 chi_sim+eng+osd、Noto CJK 字体、Python 3.12） |
| 监听 | **`127.0.0.1:8765`**（仅回环，`-p 127.0.0.1:8765:8765`）。容器内监听 `0.0.0.0:8765` |
| 对外 | 经 Cloudflare Tunnel（`cloudflared`，token 模式，路由在 Cloudflare Zero Trust 面板配置）。**服务器上没有开放任何公网 HTTP 端口** |
| Tunnel Public Hostname | `dw.wegreen.ltd` → **`http://127.0.0.1:8765`**（必须写 IPv4 字面量，不要写 `localhost`，原因见下） |
| 界面鉴权 | `DOCWEAVE_UI_AUTH=basic`，用户 `wegreen`（口令仅以 PBKDF2 哈希存储） |
| PDF 引擎 | `DOCWEAVE_PDF_ENGINE=pymupdf`（功能完整；**AGPL-3.0 网络服务义务适用**，对应源代码经 `/api/source-code` 提供） |
| 资源限制 | `--memory=3g --memory-swap=4g`。这台机器同时运行 bdr-prod / carboy / beeurei / apexterminal 四套生产服务，限制确保 DocWeave 超限时只影响自己 |
| 持久化 | 宿主 `/opt/docweave-data` → 容器 `/data`（SQLite + blobs）。**用户本地的 `data/` 没有同步上去**；库中只有下述三个合成示例项目 |
| 重启策略 | `unless-stopped`（重启机器后自动拉起） |
| 源码位置 | `/opt/docweave`（rsync，排除 `data/`、`.env`、`.venv`、`node_modules`、`testdata/`） |

### 实测（2026-09-23 重新部署后）

* 鉴权：无凭据 `401`、错误口令 `401`、正确口令 `200`。
* `/healthz` → `{"status":"ok","version":"1.0.0","instance":"normal"}`；`/readyz` → `database/storage/worker` 全部 `ok`。
* `manage.py info` → `schema v3`（带 DW-058 迁移闸门的镜像升级，旧库由 v2 就地加列，未丢数据）。
* Cloudflare 侧路由已生效：`cloudflared` 配置 v32 的 ingress 含
  `{"hostname":"dw.wegreen.ltd","service":"http://127.0.0.1:8765"}`。
* 空闲占用：**123.6 MiB / 3 GiB 上限（4.02%）**，`docker stats` 实测，此时库中已有下述三个示例项目
  （LibreOffice 仅在转换时另起进程）。

### 实例上的三个合成示例（2026-09-23）

交付实例预置了三个示例项目，覆盖三种样本模式与三类模板，全部由 `scripts/seed_samples.py`
经 `/api/v1` 创建并逐份核对过产物内容：

| 项目 | 样本模式 | 模板 | 产物 |
|---|---|---|---|
| 示例一 · 客户合同 | `per_row` | DOCX，文字字段 + 日期/金额转换 | 6 份 `.docx` |
| 示例二 · 订单对账单 | `per_group` | XLSX，单元格字段 + 重复明细行 | 3 份 `.xlsx` |
| 示例三 · 发货单 | `per_row` | PNG 图片模板 | 4 份 `.png`（尺寸与 DPI 原样） |

所有公司名、编号、金额均为编造的合成数据，模板与产物正文都写明"演示用合成内容"。
三个项目的 `origin` 均为 `api:<key id>`，因此可以用来源证据一次性清掉，不会误伤人工建的项目：

```bash
docker exec docweave python -m docweave.cli projects list        # dry-run，先看判定
docker exec docweave python -m docweave.cli projects prune --id <id> --apply
```

用于播种的临时密钥在建完后已撤销（`apikey list` 显示 `已撤销`），容器内不留明文密钥文件。

### 变更端口 / 重启

```bash
ssh vultr_tokyo
docker restart docweave                 # 重启
docker logs -f docweave                 # 日志
docker exec docweave python -m platform_identity.cli ui-user add <用户名> --password '<口令，≥8 位>'
```

改端口：修改 `-p 127.0.0.1:<新端口>:8765` 后重建容器，并同步修改 Cloudflare Tunnel 的 Public Hostname 指向。

### 回源地址必须写 `http://127.0.0.1:8765`，不能写 `localhost`

容器只把端口发布到 **IPv4 回环**：`ss -tlnp` 显示 `LISTEN 127.0.0.1:8765`，没有 IPv6 监听。
而这台主机上 `localhost` 的解析顺序是 **`::1` 在前**（`getent ahosts localhost` 首行为 `::1`）：

```
http://127.0.0.1:8765  -> 200
http://[::1]:8765      -> connection refused
http://localhost:8765  -> 200   # 仅因为 curl 在 ::1 失败后回退到 IPv4
```

依赖这种回退是脆弱的：不做回退的客户端会直接连接失败。Public Hostname 的 URL 一律写
`http://127.0.0.1:8765`。若将来确需 IPv6，要改成把端口同时发布到 `::1`。
