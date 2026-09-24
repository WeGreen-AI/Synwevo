# 运维手册（部署、备份、升级、回滚）

Vultr Tokyo 三域名部署与 CI/CD 见 [生产部署说明](../infra/production/README.md)，实际执行状态见
[部署记录](quality/deployment.md)。下面保留通用单进程与本机运行方式。

Synwevo Docs 是**单进程服务**：Web 界面、机器接口、任务运行线程与 Webhook 投递线程在同一进程内，状态放在
`SYNWEVO_DATA_DIR`（SQLite + 内容寻址文件存储）。没有额外的队列或对象存储组件。端口见
[integration/ports.md](integration/ports.md)，配置项见仓库根目录 `.env.example`。

## 1. 生产启动

**容器（推荐）**

```bash
cp .env.example .env            # 按需修改；密钥不要写进文件，用 CLI 创建
docker compose -f infra/docker-compose.yml up -d --build    # 监听 127.0.0.1:8765，数据卷 ./data
docker compose -f infra/docker-compose.yml exec synwevo python -m synwevo_docs.cli apikey create --name ci --tenant <工作空间ID> --scopes read,write
curl -s localhost:8765/readyz
```

镜像内含 LibreOffice、Tesseract（chi_sim/eng）与 Noto CJK 字体。对外请在宿主机用反向代理：界面走你自己的认证，
`/api/v1` 由 API 密钥鉴权（示例配置见 ports.md 第 3 节）。

**直接运行**

```bash
# 在仓库根目录；Python >= 3.12，Node >= 22.12（或 20.19），npm 可用
python3.12 scripts/platform.py setup
npm run typecheck
# 已有实例：先设置现有数据目录，再启动。新实例才加 --init。
export SYNWEVO_DATA_DIR=/srv/synwevo/data
.venv/bin/python scripts/platform.py up
.venv/bin/python scripts/platform.py status
.venv/bin/python scripts/platform.py down
```

`setup` 从 `infra/server/requirements.lock` 约束 Python 直接与传递依赖，从 `infra/server/requirements-build.txt` 固定构建工具，
再以 `--no-deps --no-build-isolation` 安装六个本地包；前端使用 `npm ci` 与根锁文件。
各模块的 requirements 文件继续声明所属依赖。主动升级依赖时，在干净 venv 中重新解析并更新约束，
再运行 `pip check`、三个测试套件与前端检查。版本约束不等于镜像字节可复现：基础镜像标签与 apt 包尚未锁定。

`up` 是本机三源站启动器，默认地址分别为 `http://accounts.localhost:8770`、
`http://portal.localhost:8769`、`http://docweave.localhost:8765`。它要求域名解析到回环地址；
端口可通过 `SYNWEVO_ACCOUNTS_PORT`、`SYNWEVO_PORTAL_PORT`、`SYNWEVO_DOCS_PORT` 调整。
成功以本副本进程的 `/readyz` 为准，端口被其他进程占用会失败。生产单进程按需设置鉴权与对外 URL 后，
用 `.venv/bin/python -m synwevo_server.run` 启动；拆分部署的发行者与回调配置见
[integration/ports.md](integration/ports.md)。

绑定非本机地址时必须先决定界面鉴权方式（`SYNWEVO_UI_AUTH=basic` 并 `python -m synwevo_identity.cli ui-user add`，或 `proxy`），
否则服务会拒绝启动并打印处理方式——这是为了避免「以为有代理其实裸奔」。

## 2. 持久化与备份

| 路径 | 内容 | 备份要求 |
|---|---|---|
| `data/docweave.sqlite3`（含 `-wal`、`-shm`） | 项目、模板版本、映射、任务、事件、密钥哈希 | 必须 |
| `data/blobs/` | 来源文件、模板工作副本、输出产物（按 SHA-256 存放） | 必须 |
| `data/tmp/` | 下载用临时 ZIP（超过 1 小时自动清理） | 不需要 |
| `data/regression/`（或显式配置的目录） | 回归用例（可能含业务来源与模板） | 按业务数据保护与备份，不提交 Git |

**一致备份**（服务可继续运行，命令使用上面配置的数据目录）：

```bash
.venv/bin/python scripts/platform.py backup --out /srv/synwevo/backups
.venv/bin/python scripts/platform.py verify-restore /srv/synwevo/backups/<归档文件>
```

归档工具通过 SQLite backup API 获取数据库，再校验引用的 blob。不要把运行中的数据库文件与另一个时点的
blob 目录自行拼成备份。`verify-restore` 在隔离目录核验，并读取原实例的安全日志以检查撤销记录。
备份包含账户、密钥和业务数据，应按原实例同等级保护。

**恢复**：先停止服务，再向显式目标目录恢复；已有目标需按工具提示使用 `--force`。
`SYNWEVO_DATA_DIR` 仍指向保存安全日志的原实例，以重放备份之后的撤销记录。

```bash
.venv/bin/python scripts/platform.py restore /srv/synwevo/backups/<归档文件> --into /srv/synwevo/restored
```

原实例整体丢失时，才显式使用 `--no-journal`。此模式会撤销恢复库里的全部旧 API 密钥，并停用服务客户端、
机密客户端；不会重新生成根密钥、OIDC 签名密钥或改写密码。公共交互客户端仍须重新登录。两种恢复模式都会
结束旧会话/令牌、作废未用的一次性凭据、停止排队任务，丢弃待发邮件及 Webhook，不自动续跑或外发。

安全日志只记录权限收回，不重建备份之后的新授权。若所有权已转移，恢复会应用原所有者的降权；新所有者
尚不在备份中、且没有其他有效所有者时，该空间会冻结，等待经过授权的人工核对，不让原所有者恢复旧权力。
`--no-journal` 无法判定备份之后的成员移除、账户停用、密码变更和删除。必须在隔离恢复副本核对这些变更，
按当前权限重新授权机器访问，再考虑切换服务数据目录；不能把命令成功当作可直接上线。

撤权操作的数据库提交与外部日志追加不是跨介质原子事务。管理响应中的 `journaled=false` 表示本次撤权已生效，
但恢复记录未可靠写入，须处理日志故障并补充核对，不能靠删除日志消除错误。恢复后逐文件核验 blob；下载时
已引用但缺失的产物返回 `410 artifact_missing`。

## 3. 升级与数据库迁移

1. 备份（见上）。
2. 更新代码与依赖，重新构建前端。
3. 启动：迁移在进程启动时自动执行（`Store._migrate`，前进式、可重复执行）。实际版本以 `synwevo_core.store.SCHEMA_VERSION` 与 CLI 输出为准。
4. 用 `python -m synwevo_docs.cli info` 确认 schema 版本，`/readyz` 确认就绪。

迁移测试用隔离夹具验证；生产升级前须用自己的受保护备份完成恢复演练。

**回滚**：先停止服务并确认任务状态（`python -m synwevo_docs.cli jobs list`），再恢复经过核验的备份。
不要假定旧代码能安全读取新 schema 或新的安全状态；回滚还须保留并重放之后的撤销记录。

## 4. 日常运维

```bash
python -m synwevo_docs.cli info                 # 数据目录、schema、当前监听配置、数据量
python -m synwevo_docs.cli jobs list            # 最近 20 个任务及计数
python -m synwevo_docs.cli jobs recover         # 工作进程异常退出后手动恢复（正常情况自动完成）
python -m synwevo_docs.cli apikey list|rotate|revoke
python apps/docs/scripts/regression.py                  # 回归用例（若已保存）
```

* **健康检查**：`/healthz`（存活）、`/readyz`（数据库可写、存储可写、任务线程存活）。建议把 `/readyz` 作为
  负载均衡的就绪探针，`/healthz` 作为存活探针。
* **日志**：进程 stdout/stderr。未预期错误会打印 `request_id` 与堆栈，与响应体中的 `request_id` 一致。
* **容量**：单任务样本上限 `SYNWEVO_DOCS_MAX_RECORDS_PER_JOB`（默认 20 000）；文件大小上限 `SYNWEVO_DOCS_MAX_FILE_MB`。
  PDF 导出依赖 LibreOffice 串行转换（实测约 0.5–1 秒/份，取决于机器）。基准数据见 [ACCEPTANCE.md](ACCEPTANCE.md)。
* **清理**：产物默认长期保留。需要清理时，按任务删除产物记录并移除未被引用的 blob；删除后接口返回
  `410 artifact_deleted`，界面显示对应提示（不会给出损坏文件）。

## 5. 故障处理

| 现象 | 处理 |
|---|---|
| 任务长期停在 `running` | 工作进程可能已退出：等待租约过期（默认 30 秒）自动恢复，或 `python -m synwevo_docs.cli jobs recover`；`/readyz` 的 `worker` 字段会显示 `stopped` |
| `/readyz` 返回 503 `database: error` | 磁盘满或权限问题；检查 `SYNWEVO_DATA_DIR` 可写、磁盘剩余空间 |
| 生成 PDF 报「未检测到 LibreOffice」 | 安装 LibreOffice 或设置 `SYNWEVO_DOCS_SOFFICE`；`/api/v1/capabilities` 会如实显示该能力不可用 |
| 导出 PDF 中文不显示 | 安装中文字体（容器镜像已含 Noto CJK），或用 `SYNWEVO_DOCS_CJK_FONTS` 指定字体文件 |
| Webhook 未送达 | 查看 `webhook_deliveries` 表的 `status`/`last_error`；内网地址需加入 `SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE`；回调失败不影响产物下载 |
| 接口返回 429 | 调整 `SYNWEVO_API_RATE_PER_MIN` 或在客户端退避重试（响应带 `Retry-After`） |

## 6. 账户邮件（仅发信）

唯一实现是 `services/identity/synwevo_identity/mail.py`。邀请、邮箱验证、口令找回和安全提醒均调用
`Mailer.enqueue` 写入 `mail_outbox`，账户服务的后台线程完成 SMTP 投递；业务 HTTP 不等待外部 SMTP。
这不是新增的应用间邮件 API，Docs 和门户不导入账户服务内部实现，也没有邮件管理页面。

配置示例见 [.env.example](../.env.example) 和 [生产 SMTP 示例](../infra/production/smtp.env.example)。
`MAIL_MODE` 为 `disabled`、`file` 或 `smtp`；`file` 只在本机生成明确标记的 `.eml`，不能当作送达证据。
SMTP 要求显式 `MAIL_FROM`、有效端口，以及 `SMTP_STARTTLS` / `SMTP_SSL` 恰好启用一项。
超时 `SMTP_TIMEOUT_S` 限制在 1–60 秒（每次套接字操作），默认 20 秒，当前生产设为 15 秒。
AUTH 始终在验证证书的 TLS 通道里进行。From 和 envelope sender 固定取服务端配置；业务请求不能覆盖。

本机如需真实 SMTP，将原始 `KEY=value` 文件放在 `~/.config/synwevo/smtp.env`，目录 0700、文件 0600；
密码无引号、不作 shell 展开。当前真实秘密只保存在 VPS，没有复制到开发机。本机可按下面方式加载
自己已授权的私有文件（从仓库根目录执行；已有服务需显式 `restart accounts` 才会更新其配置）：

```sh
.venv/bin/python - <<'PY'
import os, pathlib, subprocess, sys
path = pathlib.Path.home() / '.config/synwevo/smtp.env'
if path.stat().st_mode & 0o077 or path.parent.stat().st_mode & 0o077:
    raise SystemExit('SMTP 配置目录与文件不能允许其他用户读取')
child = dict(os.environ)
for line in path.read_text().splitlines():
    if not line or line.startswith('#'):
        continue
    key, value = line.split('=', 1)
    if not key.startswith(('SYNWEVO_SMTP_', 'SYNWEVO_MAIL_')):
        raise SystemExit('SMTP 文件包含不支持的配置项')
    child[key] = value
subprocess.run([sys.executable, 'scripts/platform.py', 'up', 'accounts'], env=child, check=True)
PY
```

启动器默认邮件链接使用 `http://accounts.localhost:8770`，生产使用
`https://accounts.wegreen.ltd`，均取配置而非请求 Host。启动器不会将 SMTP/MAIL 配置传给门户或 Docs。
生产按 [部署说明](../infra/production/README.md) 注入账户容器专用 env 文件；不要使用
`env`、`docker inspect` 的完整环境输出或 SMTP debug 来诊断认证问题。

投递状态与故障边界：

- `queued` 仅表示应用已提交；原子领取标记保证多个 worker 不重复领取同一行。
- `sent` 仅表示 SMTP DATA 返回 250（当前即 mailcow 接受），不是最终收件箱可见。
  日志只记录邮件 ID、250 和可用的 mailcow queue ID；可据此在服务器上关联有限投递日志。
- 明确的 SMTP 4xx、发送 DATA 之前的连接故障有限重试，间隔 30/120/600/1800/3600 秒，最多 8 次。
  同一行所有尝试保留同一 Message-ID；显式 5xx、认证或证书错误直接终止。
- `tls_certificate_invalid`、`smtp_authentication_failed`、`smtp_security_not_supported` 等是脱敏错误码，
  不存 SMTP 响应原文、认证内容或正文。配置冲突也不打印密码。
- DATA 后连接丢失、超时，或进程崩溃留下领取标记（15 分钟后判定）进入 `failed/delivery_unknown`。
  不自动重发，因为无法区分“服务器未接受”和“接受后确认丢失”。先用 Message-ID/queue ID 核实，
  再由业务方决定是否重新申请。SMTP 与 SQLite 不是分布式事务，不承诺 exactly-once。
- 完成或终止时清除邮件链接令牌；数据库权限/备份要求仍适用。恢复副本仍按现有恢复策略丢弃待发邮件，
  不自动重放。`/readyz` 正常不能代替 SMTP 或外部投递验收。

真实验收必须分别记录：应用入队、mailcow 接受、SMTP2GO 接受、目标邮件服务器接受、用户在收件箱/垃圾箱可见。
后两项需要相应服务日志或收件人确认，不能由 SMTP 250 推断。自动化回归只使用合成 localhost TLS 接收器，
不会给外部地址发信。外发验收单独授权、限制收件人及数量，不触发真实账户恢复或修改收信域名配置。

可复现的外发验收入口是 `services/identity/scripts/verify_smtp.py`。在已注入 SMTP 环境的隔离进程/容器中，
使用 `python services/identity/scripts/verify_smtp.py --data /acceptance/new-fixture --recipient '<已授权邮箱>' --send`。
父目录须为本次专用私有目录，`new-fixture` 必须不存在。脚本创建合成账户和空间，通过真实回环 HTTP
登录并调用邀请、邮箱验证接口，再只执行一次队列扫描，最多两封邮件；同一目录重复运行直接拒绝。
不挂载生产数据，不使用管理员 Cookie，不打印测试口令或邮件令牌。邮件中的链接只指向临时本地服务，
无需收件人操作。结果保存到该目录的 `acceptance.json`；保留此目录作为防重复发送标记，不盲目重跑。
