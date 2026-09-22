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
* 容器内使用 `DOCWEAVE_UI_AUTH=proxy` 表示「由外层代理完成界面认证」；若不配置任何界面鉴权且绑定非本机地址，服务会**拒绝启动**并打印处理方式。
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
