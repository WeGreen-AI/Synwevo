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
* 绑定非本机地址且 `DOCWEAVE_UI_AUTH=none` 时，`backend/run.py` **拒绝启动**并打印三种处理方式（实测：`DOCWEAVE_HOST=0.0.0.0 python backend/run.py` 退出码 1，输出「拒绝启动…」）。设为 `basic` 或 `proxy` 后正常启动。
* 容器是例外：镜像内必须绑定 `0.0.0.0`，实际暴露范围由端口映射决定，进程无法判断，因此容器内（`DOCWEAVE_IN_CONTAINER=1`，镜像已设置）**只打印警告**而不拒绝。`docker-compose.yml` 只映射到 `127.0.0.1:8765`，对外请在前面加带认证的反向代理。
* 机器接口 `/api/v1` 与此设置无关，任何情况下都需要 API 密钥。
* 反向代理示例（Nginx，界面走 SSO/Basic，机器接口走密钥）：

```nginx
location /api/v1/ { proxy_pass http://127.0.0.1:8765; }          # 机器接口：由 API 密钥鉴权
location / {                                                      # 界面：由代理认证
    auth_basic "DocWeave"; auth_basic_user_file /etc/nginx/docweave.htpasswd;
    proxy_pass http://127.0.0.1:8765;
}
location = /healthz { proxy_pass http://127.0.0.1:8765; access_log off; }
```

## 4. 端口冲突与变更

* 改端口：`DOCWEAVE_PORT=9000 python backend/run.py`（或 compose 中改映射）。
* 端口被占用时 uvicorn 会以 `[Errno 48] Address already in use` 退出；用 `lsof -nP -iTCP:8765 -sTCP:LISTEN` 查看占用进程。
* 开发时前端 5173 与后端 8765 同时运行；前端通过 `vite.config.ts` 的 proxy 访问后端，不需要额外端口。

## 5. 如何自行核对

```bash
python backend/manage.py info                       # 数据目录、schema 版本、当前监听配置
lsof -nP -iTCP -sTCP:LISTEN | grep -E '8765|5173'   # 实际监听（macOS/Linux）
curl -s localhost:8765/healthz                      # 存活
curl -s localhost:8765/readyz                       # 就绪（数据库 / 存储 / 运行线程）
curl -s -o /dev/null -w '%{http_code}\n' localhost:8765/api/v1/capabilities   # 401：机器接口需要密钥
```

实测记录（2026-09-23，macOS 26.0 / Python 3.14 / 本机部署）：`lsof` 显示 `Python … TCP 127.0.0.1:8765 (LISTEN)`；
`/healthz` → `{"status":"ok","version":"1.0.0"}`；`/readyz` → `{"status":"ready","checks":{"database":"ok","storage":"ok","worker":"ok"},"schema_version":2}`；
无密钥访问 `/api/v1/capabilities` → `401`。

---

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
| 资源限制 | `--memory=1536m --memory-swap=2048m`。这台机器同时运行 bdr-prod / carboy / beeurei / apexterminal 四套生产服务，限制确保 DocWeave 超限时只影响自己 |
| 持久化 | 宿主 `/opt/docweave-data` → 容器 `/data`（SQLite + blobs）。**用户本地的 `data/` 没有同步上去**，实例以空库交付 |
| 重启策略 | `unless-stopped`（重启机器后自动拉起） |
| 源码位置 | `/opt/docweave`（rsync，排除 `data/`、`.env`、`.venv`、`node_modules`、`testdata/`） |

### 实测（部署后）

* 鉴权：无凭据 `401`、错误口令 `401`、正确口令 `200`。
* `/healthz` → `{"status":"ok","version":"1.0.0","instance":"normal"}`；`/readyz` → `database/storage/worker` 全部 `ok`，`schema_version: 2`。
* 端到端生成（经部署实例的 HTTP 接口）：预检 2 条样本全部有效 → 任务 `completed`、2 份文档 → 产物正文为 `客户：部署验证甲 / 金额：1234.50`，即每份样本取到自己的值。验证后已删除该项目与模板、撤销临时密钥，交付状态为空库。
* 空闲占用：**88 MiB / 1.5 GiB 上限**（LibreOffice 仅在转换时另起进程）。

### 变更端口 / 重启

```bash
ssh vultr_tokyo
docker restart docweave                 # 重启
docker logs -f docweave                 # 日志
docker exec docweave python backend/manage.py ui-user add <用户名> --password '<口令，≥8 位>'
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
