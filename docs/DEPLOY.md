# 运行与部署

## 1. 本机运行（macOS / Linux）

```bash
# 1) 系统工具（可选，但决定旧格式转换、PDF 导出与 OCR 能力）
brew install --cask libreoffice          # macOS；Debian/Ubuntu: apt install libreoffice-writer libreoffice-calc libreoffice-impress
brew install tesseract                   # Debian/Ubuntu: apt install tesseract-ocr
# Linux 另外建议安装中文字体：apt install fonts-noto-cjk

# 2) 依赖、界面构建、OCR 语言数据（chi_sim/eng）
python scripts/platform.py setup

# 3) 启动（三个来源：账户服务 8770、门户 8769、DocWeave 8765）
python scripts/platform.py up
```

打开浏览器访问 `http://docweave.localhost:8765`（平台门户 `http://portal.localhost:8769`，
账户与安全 `http://accounts.localhost:8770`）。首次需要一个平台管理员：
`python -m platform_identity.cli bootstrap token`。

只要单进程形态（工作台 + 账户页同一源站，容器镜像用的就是它）：
`.venv/bin/python -m platform_server.run`，访问 `http://127.0.0.1:8765`。

数据默认保存在仓库根目录的 `data/`（SQLite + 文件），可用 `DOCWEAVE_DATA_DIR` 修改。
所有配置见 `.env.example`。

开发模式（界面热更新）：一个终端 `python scripts/platform.py up`，另一个终端 `cd apps/docweave/web && npm run dev`，访问 `http://127.0.0.1:5173`（`/api` 自动代理到 8765）。修改后端代码后需重启服务。

## 2. Docker（自有服务器）

```bash
docker compose -f infra/docker-compose.yml up -d --build            # 首次构建会下载 LibreOffice、Tesseract 与中文字体（约 1 GB）
```

* 镜像包含：API + 界面、LibreOffice（Writer/Calc/Impress）、Tesseract（chi_sim + eng）、Noto CJK 字体。
* 数据卷：`../data:/data`（构建上下文是仓库根目录）。**备份不要只复制 `.sqlite3` 文件**——
  数据库跑在 WAL 模式，主库可能落后几小时。用 `python scripts/platform.py backup`
  （走 SQLite 备份 API + blob 清单），再用 `verify-restore` 在隔离副本上真的恢复一次。
* `infra/docker-compose.yml` 默认只绑定 `127.0.0.1:8765`。
* **界面鉴权三选一**（`DOCWEAVE_UI_AUTH`）：`none`（仅限回环监听，绑定其他地址时服务拒绝启动）、
  `basic`（内置口令，`python -m platform_identity.cli ui-user add <用户名>`，口令以 PBKDF2-HMAC-SHA256 存储）、
  `proxy`（由反向代理认证，需显式声明）。对外提供服务请启用 HTTPS。
  `/api/v1` 任何情况下都需要 API 密钥，与此设置无关。
* 反向代理需允许较大的上传（例如 Nginx `client_max_body_size 100m;`），并把超时设为 300 秒以上（大文件解析/转换），
  同时转发 `Host` 与 `X-Forwarded-Proto`，否则签发出来的下载链接会指向 `http://127.0.0.1:8765`。
  完整可粘贴的配置见 `integration/ports.md` 第 3 节；也可以直接设 `DOCWEAVE_PUBLIC_URL=https://你的域名`。
* **放在 WAF / CDN（Cloudflare 等）之后时**：边缘的机器人防护会拦掉界面发出的 XHR，
  表现为「页面能打开、点任何按钮都报错」。必读 `integration/ports.md` 第 6 节。

## 3. 多用户部署建议

本版本面向单人或小团队自托管，不包含账户与权限系统。需要隔离时：为每个团队运行独立实例（独立 `DOCWEAVE_DATA_DIR` 与端口），由反向代理按路径/域名分发并认证。

## 4. 资源与限制

* 单进程运行，后台任务线程串行处理样本；LibreOffice 转换串行（每个进程一个私有配置）。
* 实测吞吐与内存见 `docs/ACCEPTANCE.md` 的「性能实测」。默认上限：单文件 100 MB、压缩包展开 600 MB、200 万单元格、2 000 页、单任务 2 万样本，可通过环境变量调整。

## 5. 升级

停止服务（`python scripts/platform.py down`）→ 备份（`python scripts/platform.py backup`，再 `verify-restore` 核验）→ 更新代码 → `python scripts/platform.py setup` → `python scripts/platform.py up`。数据库表结构在启动时自动创建，迁移前进式、可重复执行（当前 schema_version = 4；以 `python -m docweave.cli info` 的输出为准）。
