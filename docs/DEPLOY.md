# 运行与部署

## 1. 本机运行（macOS / Linux）

```bash
# 1) 系统工具（可选，但决定旧格式转换、PDF 导出与 OCR 能力）
brew install --cask libreoffice          # macOS；Debian/Ubuntu: apt install libreoffice-writer libreoffice-calc libreoffice-impress
brew install tesseract                   # Debian/Ubuntu: apt install tesseract-ocr
# Linux 另外建议安装中文字体：apt install fonts-noto-cjk

# 2) 依赖、界面构建、OCR 语言数据（chi_sim/eng）
scripts/setup.sh

# 3) 启动（默认 http://127.0.0.1:8765）
scripts/start.sh
```

打开浏览器访问 `http://127.0.0.1:8765`。数据默认保存在仓库下的 `data/`（SQLite + 文件），可用 `DOCWEAVE_DATA_DIR` 修改。所有配置见 `.env.example`。

开发模式（界面热更新）：一个终端 `scripts/start.sh`，另一个终端 `cd frontend && npm run dev`，访问 `http://127.0.0.1:5173`（`/api` 自动代理到 8765）。修改后端代码后需重启服务。

## 2. Docker（自有服务器）

```bash
docker compose up -d --build            # 首次构建会下载 LibreOffice、Tesseract 与中文字体（约 1 GB）
```

* 镜像包含：API + 界面、LibreOffice（Writer/Calc/Impress）、Tesseract（chi_sim + eng）、Noto CJK 字体。
* 数据卷：`./data:/data`。备份时停止服务后复制该目录即可（SQLite + 内容寻址文件）。
* `docker-compose.yml` 默认只绑定 `127.0.0.1:8765`。**本软件不内置登录**：对外提供服务时请放在带认证的反向代理（如 Nginx + Basic Auth / OIDC）之后，并启用 HTTPS。
* 反向代理需允许较大的上传（例如 Nginx `client_max_body_size 100m;`），并把超时设为 300 秒以上（大文件解析/转换）。

## 3. 多用户部署建议

本版本面向单人或小团队自托管，不包含账户与权限系统。需要隔离时：为每个团队运行独立实例（独立 `DOCWEAVE_DATA_DIR` 与端口），由反向代理按路径/域名分发并认证。

## 4. 资源与限制

* 单进程运行，后台任务线程串行处理样本；LibreOffice 转换串行（每个进程一个私有配置）。
* 实测吞吐与内存见 `docs/ACCEPTANCE.md` 的「性能实测」。默认上限：单文件 100 MB、压缩包展开 600 MB、200 万单元格、2 000 页、单任务 2 万样本，可通过环境变量调整。

## 5. 升级

停止服务 → 备份 `data/` → 更新代码 → `scripts/setup.sh` → 启动。数据库表结构在启动时自动创建（当前 schema_version = 1）。
