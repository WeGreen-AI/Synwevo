# 运维手册（部署、备份、升级、回滚）

DocWeave 是**单进程服务**：Web 界面、机器接口、任务运行线程与 Webhook 投递线程在同一进程内，状态放在
`DOCWEAVE_DATA_DIR`（SQLite + 内容寻址文件存储）。没有额外的队列或对象存储组件。端口见
[integration/ports.md](integration/ports.md)，配置项见仓库根目录 `.env.example`。

## 1. 生产启动

**容器（推荐）**

```bash
cp .env.example .env            # 按需修改；密钥不要写进文件，用 CLI 创建
docker compose up -d --build    # 监听 127.0.0.1:8765，数据卷 ./data
docker compose exec docweave python backend/manage.py apikey create --name ci --scopes read,write
curl -s localhost:8765/readyz
```

镜像内含 LibreOffice、Tesseract（chi_sim/eng）与 Noto CJK 字体。对外请在宿主机用反向代理：界面走你自己的认证，
`/api/v1` 由 API 密钥鉴权（示例配置见 ports.md 第 3 节）。

**直接运行**

```bash
backend/.venv/bin/pip install -r backend/requirements.txt     # 或 requirements-pdfium.txt（AGPL-free）
cd frontend && npm ci && npm run build && cd ..
DOCWEAVE_HOST=127.0.0.1 backend/.venv/bin/python backend/run.py
```

绑定非本机地址时必须先决定界面鉴权方式（`DOCWEAVE_UI_AUTH=basic` 并 `manage.py ui-user add`，或 `proxy`），
否则服务会拒绝启动并打印处理方式——这是为了避免「以为有代理其实裸奔」。

## 2. 持久化与备份

| 路径 | 内容 | 备份要求 |
|---|---|---|
| `data/docweave.sqlite3`（含 `-wal`、`-shm`） | 项目、模板版本、映射、任务、事件、密钥哈希 | 必须 |
| `data/blobs/` | 来源文件、模板工作副本、输出产物（按 SHA-256 存放） | 必须 |
| `data/tmp/` | 下载用临时 ZIP（超过 1 小时自动清理） | 不需要 |
| `regression/` | 回归用例（如已创建） | 建议随代码一起提交 |

**一致备份**（服务可继续运行）：

```bash
sqlite3 data/docweave.sqlite3 ".backup '/backup/docweave-$(date +%F).sqlite3'"
rsync -a --delete data/blobs/ /backup/blobs/
```

**恢复**：停止服务 → 还原上述两项（数据库与 blobs 必须来自同一时间点）→ 启动 → `curl /readyz`。
数据库里引用的 blob 若缺失，下载会返回 `410 artifact_missing`，不会返回损坏文件。

## 3. 升级与数据库迁移

1. 备份（见上）。
2. 更新代码与依赖，重新构建前端。
3. 启动：迁移在进程启动时自动执行（`Store._migrate`，前进式、可重复执行）。当前 `schema_version = 3`（v2 → v3 为 `projects.origin` / `projects.archived_at` 加列；迁移闸门见 DW-058，旧库就地升级不丢数据）。
4. 用 `python backend/manage.py info` 确认 schema 版本，`/readyz` 确认就绪。

v1 → v2 迁移内容：新增租户列、任务租约/运行记录/事件表、幂等表、API 密钥表、Webhook 投递表；
旧数据保持可读（历史项目与任务归属 `default` 租户）。迁移在真实 v1 数据库副本上验证通过。

**回滚**：本服务不做破坏性迁移（只增列/增表），旧版本代码可以直接读回新库中的旧字段；
如需完全回退，停止服务并还原备份即可。回滚前请确认没有正在运行的任务（`manage.py jobs list`）。

## 4. 日常运维

```bash
python backend/manage.py info                 # 数据目录、schema、当前监听配置、数据量
python backend/manage.py jobs list            # 最近 20 个任务及计数
python backend/manage.py jobs recover         # 工作进程异常退出后手动恢复（正常情况自动完成）
python backend/manage.py apikey list|rotate|revoke
python scripts/regression.py                  # 回归用例（若已保存）
```

* **健康检查**：`/healthz`（存活）、`/readyz`（数据库可写、存储可写、任务线程存活）。建议把 `/readyz` 作为
  负载均衡的就绪探针，`/healthz` 作为存活探针。
* **日志**：进程 stdout/stderr。未预期错误会打印 `request_id` 与堆栈，与响应体中的 `request_id` 一致。
* **容量**：单任务样本上限 `DOCWEAVE_MAX_RECORDS_PER_JOB`（默认 20 000）；文件大小上限 `DOCWEAVE_MAX_FILE_MB`。
  PDF 导出依赖 LibreOffice 串行转换（实测约 0.5–1 秒/份，取决于机器）。基准数据见 [ACCEPTANCE.md](ACCEPTANCE.md)。
* **清理**：产物默认长期保留。需要清理时，按任务删除产物记录并移除未被引用的 blob；删除后接口返回
  `410 artifact_deleted`，界面显示对应提示（不会给出损坏文件）。

## 5. 故障处理

| 现象 | 处理 |
|---|---|
| 任务长期停在 `running` | 工作进程可能已退出：等待租约过期（默认 30 秒）自动恢复，或 `manage.py jobs recover`；`/readyz` 的 `worker` 字段会显示 `stopped` |
| `/readyz` 返回 503 `database: error` | 磁盘满或权限问题；检查 `DOCWEAVE_DATA_DIR` 可写、磁盘剩余空间 |
| 生成 PDF 报「未检测到 LibreOffice」 | 安装 LibreOffice 或设置 `DOCWEAVE_SOFFICE`；`/api/v1/capabilities` 会如实显示该能力不可用 |
| 导出 PDF 中文不显示 | 安装中文字体（容器镜像已含 Noto CJK），或用 `DOCWEAVE_CJK_FONTS` 指定字体文件 |
| Webhook 未送达 | 查看 `webhook_deliveries` 表的 `status`/`last_error`；内网地址需加入 `DOCWEAVE_WEBHOOK_ALLOW_PRIVATE`；回调失败不影响产物下载 |
| 接口返回 429 | 调整 `DOCWEAVE_API_RATE_PER_MIN` 或在客户端退避重试（响应带 `Retry-After`） |
