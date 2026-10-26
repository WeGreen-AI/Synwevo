# 运维手册（部署、备份、升级、回滚）

Vultr Tokyo 三域名部署与 CI/CD 见 [生产部署说明](../infra/production/README.md)，实际执行状态见
[部署记录](quality/deployment.md)。下面保留通用单进程与本机运行方式。

Synwevo Docs 是**单进程服务**：Web 界面、机器接口、任务运行线程与 Webhook 投递线程在同一进程内，状态放在
`SYNWEVO_DATA_DIR`（SQLite + 内容寻址文件存储）。没有额外的队列或对象存储组件。端口见
[integration/ports.md](integration/ports.md)，配置项见仓库根目录 `.env.example`。

## 当前本机产品轮：开放注册与隔离邮件

2026-09-25 本轮不操作生产。当时 `main` 推送会触发生产工作流，所以本轮成果暂不推送（现在部署只来自合入 `dev`，
见 [AGENTS.md](../AGENTS.md#分支与发布)）。
从仓库根启动独立验收环境（选择未使用的数据目录；`--init` 只用于新隔离实例）：

```sh
export SYNWEVO_DATA_DIR=/private/tmp/synwevo-local-acceptance/data
export SYNWEVO_TEST_INSTANCE=1
export SYNWEVO_MAIL_MODE=file
export SYNWEVO_MAIL_DIR=/private/tmp/synwevo-local-acceptance/mail
export SYNWEVO_REGISTRATION=open
export SYNWEVO_ACCOUNTS_PORT=28770
export SYNWEVO_PORTAL_PORT=28769
export SYNWEVO_DOCS_PORT=28765
.venv/bin/python scripts/platform.py up --init
.venv/bin/python scripts/platform.py status
```

实际账户/门户/Docs 地址分别是 `http://accounts.localhost:28770`、`http://portal.localhost:28769`、
`http://docweave.localhost:28765`。启动器在 IPv4 与可用 IPv6 回环同时监听，禁止将此便利模式绑定公网。
共享库与应用数据仍在一个显式目录，端口和数据目录不会覆盖生产配置。
已有库的 `instance_kind` 不会因后来设置 TEST_INSTANCE 变成测试库。

环境 `REGISTRATION=open` 启用注册；管理员保存的 `meta.registration.mode` 若存在则优先，
管理页支持 open/invite/closed 并由真实注册端点执行。公开注册不授予管理员，邮箱验证前不能进入业务空间。
新实例的首位管理员沿用交互 CLI 引导，不是第一个注册者。正常用户仍能使用注册/登录，不被管理初始化表单阻挡。

`.eml` 只落在 0700 目录、0600 文件，明确标记 LOCAL FILE SINK；它包含短期链接，应按秘密保护，
不能提交、截图或粘贴到日志。三种语言的模板由账户偏好/邀请时选择固定，后台重试不改语言。
共享 HTTP 工厂对 Uvicorn 访问日志统一移除查询串，避免保存回调授权码、签名参数和查询个人信息；
仍保留路径/状态，业务审计与 request ID 保留原契约。另行部署的代理日志仍需按相同原则配置。
文件接收器成功不证明 SMTP 入队、中继、远端接受或最终收件箱可见。本轮不向外部收件人发送验收邮件。
SMTP 的已授权配置方式仍见第 6 节，不能为本轮自动获取 VPS 秘密或修改邮件服务。

结束当前实例需保留相同数据目录和端口变量，再执行 `.venv/bin/python scripts/platform.py down`。
不要按名称清理账号或删除用户实例的数据。

## 1. 生产启动

**容器（推荐）**

```bash
cp .env.example .env            # 按需修改；密钥不要写进文件，用 CLI 创建
# 从 git archive 构建：镜像只含受版本控制的文件，并记下源代码发布清单（/api/source-code 只打包清单上的文件）。
# 未提交的修改用 `git stash create` 生成的临时提交带上（工作树干净时它不输出，退回 HEAD）。
rev=$(git stash create); git archive --format=tar "${rev:-HEAD}" | docker build -f infra/Dockerfile -t synwevo:local -
docker compose -f infra/docker-compose.yml up -d             # 监听 127.0.0.1:8765，数据卷 ./data
docker compose -f infra/docker-compose.yml exec synwevo python -m synwevo_docs.cli apikey create --name ci --tenant <工作空间ID> --scopes read,write
curl -s localhost:8765/readyz
```

`docker compose … up -d --build` 仍可用，但它从工作树构建：上下文不是 `git archive`，构建日志会提示没有写入
源代码发布清单，这样的镜像对 `/api/source-code` 返回 503（分不清源代码与工作树里的其他文件时不提供，
而不是把备份、密钥一起发出去）。对外提供服务时请用上面的 `git archive` 构建。

也可以先把 `git archive` 的发布包（例如代码托管平台按提交生成的 tar.gz）解包到目录，再在该目录里
`docker build -f infra/Dockerfile .`（部署接收器与 CI 就是这样做的）——但解包之后不要在目录里新增或改动文件。
提交号盖在 `source_manifest.py` 里，只能证明这一个文件来自 `git archive`；构建还核对上下文里**每个**文件的修改时间
都是该提交的时间（`git archive` 给每个文件的时间，tar 解包与 `docker build` 都保留它）。有一个对不上——解包后放进去的
证书、密钥、备份，或改过的源文件——构建日志就列出它们、不写发布清单，镜像对 `/api/source-code` 返回 503。
要改代码就提交后重新 `git archive`；不能用会丢弃文件时间的方式解包或复制（`tar -m`、不带 `-p` 的 `cp -r`）。
这项核对在镜像的单独阶段里进行，对象是一次复制进来的**整个**构建上下文，而不是最后阶段按目录复制的层：Docker 的层缓存
不看文件时间，内容没变的目录（多数部署里的 `packages/`）会从更早的构建原样取回、带着那次的时间。所以上下文里多出来的
任何文件——即使镜像并不复制它——也会让构建不写清单（部署接收器自己的 `.archive-sha256` 由 `.dockerignore` 挡在上下文外）。
同一提交、内容完全相同的重新构建会沿用上一次的结果，拒绝也一样：如果是解包丢了文件时间而被拒，正确解包后用
`docker build --no-cache` 重新构建。

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

从检出运行时，`/api/source-code` 按 `git ls-files` 打包。服务若以另一个账户运行、而仓库属于运营方账户，
git（2.35.2 起）会以 “dubious ownership” 拒绝读取，`/api/source-code` 返回 503 并说明原因。确认信任该目录后，
以**运行服务的账户**执行 `git config --global --add safe.directory <安装目录>`（或用 `--system` 写入系统配置）；
服务不替你跳过这项检查，也不读取 `GIT_*` 环境变量。

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
`--no-journal` 恢复出的库从此开始一段新的日志历史（库内 `journal_epoch`）：以后从这段历史的备份做
带日志的恢复时，早于它、且库里没有回执的预写撤销记录不再重放——否则被放弃的那段历史里轮换出的旧密钥
会重新生效，操作员选择不重放的撤销也会重新落地。

备份时间之后的撤销照常重放。早于备份时间、备份里却没有回执的预写记录，多数是备份恰好在其提交前拍下，
照常重放；若备份里有晚于该记录、不晚于备份时间提交的变更（其他撤销的回执，或该对象本身的改动），说明
那次尝试从未提交（进程在提交前停止），恢复跳过并作废它，不会撤回之后合法的重新授予。恢复报告单独列出
早于备份时间而重放的预写记录，供人工核对。

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

### 4.0 HTTP 安全头

三个服务的每个响应都由共享网关加上 OWASP ASVS V14.4 要求的安全头（清单见 [api.md](integration/api.md) §2）。
HSTS 只在请求经 TLS 到达时发出——生产 nginx 设置 `X-Forwarded-Proto https`，本机 HTTP 不会把浏览器钉在 HTTPS 上。
CSP 是**强制**模式；若某次发布后浏览器控制台出现 `Content Security Policy` 拦截（三套浏览器回归在提交前都会因此失败），
可在账户/门户/Docs 的 `runtime.env` 里设置 `SYNWEVO_CSP="<替代策略>"` 或 `SYNWEVO_CSP=off` 并重启对应服务作为临时措施，
然后修正策略再发布；不要长期保留 `off`。

生产在 Cloudflare 之后：区域开启了 Cloudflare Web Analytics 时，边缘会把 `static.cloudflareinsights.com/beacon.min.js` 注入每个 HTML
页面。带 `CF-Ray` 的请求因此得到默认策略的 Cloudflare 变体（仅多出该脚本源与其 `cloudflareinsights.com` 上报源，其余不变）；
不经 Cloudflare 的请求仍是严格策略。不想要这个信标就在 Cloudflare 控制台关闭 Web Analytics，策略无需改动。


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
* **容量**：单任务样本上限 `SYNWEVO_DOCS_MAX_RECORDS_PER_JOB`（默认 20 000）；文件大小上限 `SYNWEVO_DOCS_MAX_FILE_MB`；
  XLSX 重复内容块一份文档最多写入的单元格数 `SYNWEVO_DOCS_MAX_BLOCK_CELLS`（默认 250 000；数的是实际写入的——各行模板单元格与字段写入的列，每份、每条明细，文档内所有块合计）——实测每写入一个单元格约占 1.5 KB 常驻内存
  （40 万个峰值 649 MB），默认值让一次填充约 420 MB；调高前先看工作进程的内存上限（验证容器与生产工作进程约 3 GB）。
  PDF 导出依赖 LibreOffice 串行转换（实测约 0.5–1 秒/份，取决于机器）。基准数据见 [ACCEPTANCE.md](ACCEPTANCE.md)。
* **清理**：产物默认长期保留。**目前没有**按任务删除产物或回收未引用 blob 的工具（`artifacts.deleted_at`
  会被读取、从未被写入；读到它时接口返回 `410 artifact_deleted`）。回收的设计、前置条件与需要持有人决定的事项见
  [platform/blob-reclamation.md](platform/blob-reclamation.md)；在那之前不要手工删除 `blobs/` 下的任何文件——
  一个 blob 可能同时被 Docs 来源、消息附件、组织发布和任务快照引用。只读清点：`apps/docs/scripts/blob_audit.py`。

## 5. 故障处理

| 现象 | 处理 |
|---|---|
| 任务长期停在 `running` | 工作进程可能已退出：等待租约过期（默认 30 秒）自动恢复，或 `python -m synwevo_docs.cli jobs recover`；`/readyz` 的 `worker` 字段会显示 `stopped` |
| `/readyz` 返回 503 `database: error` | 磁盘满或权限问题；检查 `SYNWEVO_DATA_DIR` 可写、磁盘剩余空间 |
| 生成 PDF 报「未检测到 LibreOffice」 | 安装 LibreOffice 或设置 `SYNWEVO_DOCS_SOFFICE`；`/api/v1/capabilities` 会如实显示该能力不可用 |
| 导出 PDF 中文不显示 | 安装中文字体（容器镜像已含 Noto CJK），或用 `SYNWEVO_DOCS_CJK_FONTS` 指定字体文件 |
| Webhook 未送达 | 查看 `webhook_deliveries` 表的 `status`/`last_error`；内网地址需加入 `SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE`；回调失败不影响产物下载 |
| 接口返回 429 | 看 `details.limit_scope`/`policy_source`（或管理台「调用记录」按请求编号查）：在「流量与配额」调整对应层的设置，或在客户端按 `Retry-After` 退避；部署默认值为 `SYNWEVO_API_RATE_PER_MIN`（每凭据）与 `SYNWEVO_API_WORKSPACE_RATE_PER_MIN`（每工作空间合计） |
| 来源文件停在「安全检查暂不可用」（`scan_failed`）、`/readyz` 的 `malware_scanner` 为 `down: …` | clamd 不可达：`docker compose logs clamav`（首次启动要先下载病毒库，`start_period` 15 分钟）；恢复后文件按 30 s→10 min 的间隔自动重试，不需要重新上传。文件超过扫描上限（`scan.status=too_large`）则调大 `SYNWEVO_CLAMD_MAX_BYTES` 与 clamd 的 `StreamMaxLength` 后让用户重新上传 |
| 上传返回 400 `malware_detected`、文件显示「安全检查未通过」 | 引擎命中签名（`scan.signature`、`scan.engine` 记录了判定时的引擎与病毒库）。字节已从 blob 存储删除；没有"仍然处理"的入口。怀疑误报按 docs/security/file-acceptance.md §8 处理 |
| 上传返回 400 `encrypted_file`（「文件设有密码或含有加密内容…」）或 `unchecked_file`（「文件的结构无法安全地完成检查…」），或来源显示「错误」且 `scan.status=encrypted` | 文件有打开密码，或（`scan.checked=false`、`unchecked_file`）结构读不完——截断、超出识别器上限，同样不能当作干净；接纳日志的 `scan.encrypted … checked=False detail=…` 写着是什么让检查停下（`detail.kind`/`scan.reason`：`archive`/`office`/`pdf`）。安全检查看不到里面：不是恶意软件，也不会被接纳。请用户去掉密码或另存不加密的副本后重新上传；没有"跳过检查"的入口。补扫对历史文件报告的 `encrypted` 不改访问，处理方式由所有者决定（file-acceptance §8.1） |
| 模板上传返回 503 `scan_unavailable` | 模板是同步扫描：clamd 不可达时拒绝并给 `Retry-After`，clamd 恢复后重试即可 |

## 6. 账户邮件（仅发信）

唯一实现是 `services/identity/synwevo_identity/mail.py`。邀请、邮箱验证、口令找回和安全提醒均调用
`Mailer.enqueue` 写入 `mail_outbox`，账户服务的后台线程完成 SMTP 投递；业务 HTTP 不等待外部 SMTP。
这不是新增的应用间邮件 API，Docs 和门户不导入账户服务内部实现。平台管理页仅提供脱敏运行状态与
经过重认证的有限重试，不展示 SMTP 密码、邮件正文或令牌，不把 SMTP 接受等同于最终收件。

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

### 6.1 机构邮箱（学校/公司）投递与排障

一封验证邮件从提交到「人看见」要过五道门，每道门的证据在不同地方；前四道可以在本机或 VPS 上只读核对，第五道只有
SMTP2GO 账户或收件人能看：

| 环节 | 在哪里看 | 只读命令 / 位置 |
|---|---|---|
| 应用入队与状态 | 账户库 `mail_outbox`（`sent` = mailcow DATA 250） | 平台管理页「邮件」，或容器内 `sqlite3 'file:/data/docweave.sqlite3?mode=ro'` 按 `ref_id` 查 |
| mailcow 接受 / DKIM | postfix 与 rspamd 日志（队列 ID、`DKIM_SIGNED{hikosphere.com:s=mailcow2026}`） | `docker logs mailcowdockerized-postfix-mailcow-1 \| grep <queue id>` |
| 中继接受 | postfix `relay=mail.smtp2go.com … status=sent (250 OK id=<SMTP2GO id>)` | 同上；这个 id 是向 SMTP2GO 查询的钥匙 |
| 收件网关裁决 | 只在 SMTP2GO Reports → Activity（按 id 搜索）里：delivered / deferred / bounced 与对方服务器回话 | 账户持有人 |
| 用户可见 | 收件箱、垃圾箱、机构隔离区通知（CUHK 两个域的 MX 都是 Trend Micro Email Security） | 收件人 |

排查时把地址的本地部分遮掉；不要导出正文或令牌。`docker logs` 只保留最近三份 10 MB，超过两周的记录要看 SMTP2GO。

核对发信域时看三项（都是公开 DNS，用 `dig TXT` 即可）：SPF 是否包含实际的出口（本链路是 SMTP2GO 中继）、
DKIM 选择器（本部署是 `mailcow2026._domainkey`）是否发布且与 rspamd 持有的公钥一致、DMARC 的策略与 `rua`。
某一天的记录快照不写在这里，查询结果以 DNS 为准（2026-10-25 删去 2026-09-26 的快照）。要提高机构网关的接受率，按顺序做（都是 DNS/供应商侧，不改代码）：

1. SMTP2GO 后台验证发件域 `hikosphere.com`（按其给出的 CNAME 记录发布），这样回信地址（Return-Path）与 DKIM 都与 From 对齐，
   退信也会回到可查的地方；同时给事务邮件关闭「链接跟踪 / 打开跟踪」，否则 SMTP2GO 重写正文会破坏 mailcow 的签名，机构网关还会再重写一次链接。
2. SPF 加 `include:spf.smtp2go.com`（若 SMTP2GO 使用自己的回信域，这一条不影响 DMARC，但让 From 域的 SPF 记录与实际出口一致）。
3. DMARC 从 `p=none` 起步并把 `rua` 指向能看到的报表；对齐验证一周无失败后升到 `quarantine`，再到 `reject`。
4. 评估把 From 改到产品域 `wegreen.ltd`（链接域与发件域一致是常见评分项）：需要 mailcow 里该域的信箱或 send-as 授权，
   再改 `smtp.env` 的 `SYNWEVO_MAIL_FROM` 并重启 `accounts`。

应用侧已经做的（CI-24）：正文 quoted-printable；`Auto-Submitted`/`X-Auto-Response-Suppress`；令牌只在链接的 `#` 片段里且必须在页面上确认，
所以网关的预取/时点点击重写不会消费它；收件页写明发件地址、网关延迟/隔离与重发规则；已有账户的地址再注册寄「账户已经存在」提示而不是沉默。

## 7. 登录方式：保持登录、邮箱验证码与外部身份提供商

账户服务提供四种登录路径：口令、Passkey、邮箱一次性验证码、外部 OpenID 提供商；契约见
[sessions.md](security/sessions.md) §2.1、§4.4–§4.6。运维上要配置的只有下面几项。

| 项 | 怎么做 |
|---|---|
| 邮箱验证码 | 复用账户服务的 SMTP（§6）。`SYNWEVO_MAIL_MODE=disabled` 时验证码请求仍返回同形 202，但不发信；登录页会把 `delivery=unavailable` 解释为「邮件服务尚未配置」。 |
| 保持登录 | 默认即生效；`SYNWEVO_SESSION_PERSISTENT_IDLE_S`（默认 14 天）可调，绝对过期仍由 `SYNWEVO_SESSION_ABSOLUTE_S` 决定。平台管理员忽略此项。 |
| 外部提供商 | 按下面的配置表在提供商处登记 Web 应用，把变量写入**账户服务**的 `runtime.env`（0600，与 SMTP 秘密同样只给 accounts 容器；保留文件里已有的其他行），只重启 accounts 一个服务。 |

### 7.1 Google / Microsoft 配置表（本项目实际使用的值）

提供商 id 是固定的 `google` 与 `microsoft`（`synwevo_identity.external.KNOWN`），不是占位符。生产
`SYNWEVO_ISSUER=https://accounts.wegreen.ltd`，因此回调地址是下面这两条，必须逐字登记：

| 项 | Google | Microsoft |
|---|---|---|
| 提供商 id | `google` | `microsoft` |
| 在哪里登记 | Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client ID，类型 **Web application**；OAuth consent screen 需先建立（外部用户类型；正式对外前把发布状态从 Testing 切到 In production，否则只有测试用户名单能登录） | Microsoft Entra 管理中心 → App registrations → New registration；平台选择 **Web** |
| 回调地址（Authorized redirect URI / Redirect URI） | `https://accounts.wegreen.ltd/auth/external/google/callback` | `https://accounts.wegreen.ltd/auth/external/microsoft/callback` |
| 授权的 JavaScript 来源（Google 要求） | `https://accounts.wegreen.ltd` | — |
| 支持的账户类型 | 任何 Google 账户（个人与 Workspace） | 由注册时的 *Supported account types* 决定，并与 `SYNWEVO_IDP_MICROSOFT_TENANT` 对应：`common`（个人 + 任意组织，默认）、`organizations`（仅工作/学校账户）、`consumers`（仅个人）、或具体 tenant id（仅该组织）。发行者模板 `https://login.microsoftonline.com/{tenant}/v2.0`；`common`/`organizations`/`consumers` 时 id_token 的 `iss` 按令牌里的 `tid` 校验。 |
| 最小 scopes | `openid email profile`（默认值；不申请 Gmail、Drive 或任何读写邮件/文件的权限） | `openid email profile`（默认值；不申请 Graph 的邮件/文件权限） |
| 客户端类型 / 凭据 | Web 应用，机密客户端：client id + client secret | Web 应用，机密客户端：Application (client) ID + Certificates & secrets 里新建的 client secret **值**（不是 secret id）；记下过期日期 |
| 需要写入 `runtime.env` 的变量 | `SYNWEVO_IDP_PROVIDERS` 含 `google`；`SYNWEVO_IDP_GOOGLE_CLIENT_ID`；`SYNWEVO_IDP_GOOGLE_CLIENT_SECRET` | `SYNWEVO_IDP_PROVIDERS` 含 `microsoft`；`SYNWEVO_IDP_MICROSOFT_CLIENT_ID`；`SYNWEVO_IDP_MICROSOFT_CLIENT_SECRET`；`SYNWEVO_IDP_MICROSOFT_TENANT`（省略即 `common`） |
| 可选 | `SYNWEVO_IDP_GOOGLE_LABEL`（按钮文字）、`SYNWEVO_IDP_GOOGLE_SCOPES` | `SYNWEVO_IDP_MICROSOFT_LABEL`、`SYNWEVO_IDP_MICROSOFT_SCOPES`、`SYNWEVO_IDP_MICROSOFT_ISSUER`（一般不需要） |
| 邮箱声明 | `email_verified=false` 或缺失时不注册、不匹配现有账户，只允许口令登录后在账户页按 `sub` 关联 | 同左；Microsoft 个人账户的 `email` 常为未验证声明，按同一规则处理 |

无秘密示例（`.env.example` 同样有）：

```
SYNWEVO_IDP_PROVIDERS=google,microsoft
SYNWEVO_IDP_GOOGLE_CLIENT_ID=<Google OAuth client id>
SYNWEVO_IDP_GOOGLE_CLIENT_SECRET=<在受保护文件里填写，不进聊天、不进仓库>
SYNWEVO_IDP_MICROSOFT_CLIENT_ID=<Entra application (client) id>
SYNWEVO_IDP_MICROSOFT_CLIENT_SECRET=<同上>
SYNWEVO_IDP_MICROSOFT_TENANT=common
```

### 7.2 配置检查与启用步骤

1. 填好 `runtime.env` 后 `docker compose restart accounts`（或等价的单服务重启）。启动时校验：列出的
   提供商缺任一凭据，账户服务**拒绝启动**并在日志里点名缺失的变量；没有列出的提供商不出现在登录页
   （`/api/auth/state.external_providers` 为空）。
2. 以平台管理员打开 **平台管理 → 登录方式**（`GET /api/admin/identity-providers`）：每个提供商显示已启用 /
   未配置、要登记的精确回调地址、scopes、发行者、客户端 id 前 8 位、密钥是否已填写、已关联账户数；
   「立即检查连接」实时拉取发现文档并报告可达与否。这一页**永不显示秘密，也不能输入秘密**。
3. 用一个受控账户走一遍「登录页 → 提供商 → 回到账户页」再对外宣布；本机可用
   `services/identity/scripts/idp_double.py`（测试替身，非真实提供商）预演完整链路。关联按提供商稳定 `sub`
   进行，不凭邮箱合并账户。
4. 平台管理员用外部提供商或邮箱验证码登录后得到的是**一因素会话**：账户功能正常，平台管理与 API 管理
   返回 `403 step_up_required`，账户页顶部提示输入口令确认；口令确认后 `amr` 变为 `idp:<id>+password` /
   `email+password`，管理页即可使用。见 [sessions.md](security/sessions.md) §4.6。不再全局禁止管理员使用
   这两种登录方式。

### 7.3 尚需账户持有人完成的步骤（生产）

生产的 `runtime.env` 目前**没有**任何外部提供商（登录页无按钮，管理页「登录方式」两卡均为未配置）。需要
持有 Google Cloud 项目与 Microsoft Entra 租户管理权的人：按 7.1 登记两个 Web 应用（回调逐字），在
VPS 上编辑 `/opt/synwevo/runtime.env` 追加上表变量，`docker compose restart accounts`，然后按 7.2 第 2、3
步验证。代理不能替代这一步，也不要把 client secret 贴进聊天或提交。

## 8. 上传内容的恶意软件扫描（ClamAV）

策略、状态机与验收在 `docs/security/file-acceptance.md`。运维要点：

| 项 | 值 |
|---|---|
| 服务 | `infra/production/compose.yaml` 的 `clamav`（官方镜像 `clamav/clamav:1.4`，GPLv2，独立容器，端口只在内网） |
| Docs 环境 | `SYNWEVO_CLAMD_ADDRESS=tcp://clamav:3310`、`SYNWEVO_MALWARE_SCAN=required`；可选 `SYNWEVO_CLAMD_TIMEOUT_S`（60）、`SYNWEVO_CLAMD_MAX_BYTES`（25 MiB，与 clamd `StreamMaxLength` 一致） |
| 病毒库 | 容器内 freshclam 每小时检查 `database.clamav.net`（VPS 需允许出站 HTTPS）；库持久化在 `/opt/synwevo/clamav`，重建容器不重新下载 |
| 内存 | 守护进程加载 3.6 M 条签名后约 1 GiB 常驻（本机 ClamAV 1.5.4 实测 950 MiB），重载新库时短暂翻倍；服务限额 2 GiB。**加入前确认 VPS 余量**（当前四个服务限额合计约 4 GiB） |
| 上线顺序 | 合并 compose → `docker compose up -d clamav` → 等 `healthy`（首次 5–15 分钟）→ 重建 `docs`。Docs 不依赖 clamav 健康即可启动：引擎未就绪时新文件停在隔离态并自动重试，已接纳的文件照常 |
| 观察 | `/readyz` 的 `checks.malware_scanner`（`ok`/`off`/`down: …`，不影响就绪判定）；`/api/v1/capabilities` 的 `environment.malware_scan`；审计事件 `source.rejected`（见 file-acceptance §8） |
| 回滚 | 从 docs 服务去掉两个环境变量并重建；已写入的 `scan` 列保留，`rejected` 的行保持拒绝 |
| 不做 | 不把用户文件送往 VirusTotal 等外部平台；不提供跳过扫描的按钮；不用测试密钥或关闭检查让流水线变绿 |
| 加密文件（§8.7） | 开启扫描时，设有密码的压缩包、Office 文档和要密码才能打开的 PDF 一律不接纳（400/410 `encrypted_file`，不是恶意软件、不进隔离区）。顶层的加密由应用自己识别；clamd 加 `AlertEncryptedArchive yes` 后，**嵌在文档或压缩包里的**加密压缩包也会以 `Heuristics.Encrypted.*` 报出（应用同样当作"加密、不接纳"）。只开这一项：`AlertEncrypted`/`AlertEncryptedDoc` 会把只设权限密码、不需要密码就能打开的 PDF 也报出来 |

**clamd 选项 `AlertEncryptedArchive yes`（主机侧一次性步骤；参考 compose 已加挂载）。** 官方镜像只接受挂载进来的
配置文件，所以用同一标签镜像自带的 `clamd.conf` 加一行，挂到 `/etc/clamav/clamd.conf`（只读）。**先建文件再合并 compose**：
文件不存在时 Docker 会在那个位置建一个目录，clamd 起不来。换镜像标签时按同样步骤重新生成。

```bash
cd /opt/synwevo/config
docker run --rm --entrypoint cat clamav/clamav:1.4 /etc/clamav/clamd.conf > clamd.conf.new
grep -q '^AlertEncryptedArchive' clamd.conf.new || printf '\nAlertEncryptedArchive yes\n' >> clamd.conf.new
grep -n '^AlertEncrypted' clamd.conf.new          # 只应有一行：AlertEncryptedArchive yes
install -m 0644 clamd.conf.new clamd.conf && rm clamd.conf.new
# 先用一次性容器确认镜像读得懂这份文件（不碰正在运行的服务）：
docker run --rm -v /opt/synwevo/config/clamd.conf:/etc/clamav/clamd.conf:ro --entrypoint clamconf clamav/clamav:1.4 -n | grep -i alertencrypted
# 把 infra/production/compose.yaml 里 clamav 的这一行挂载并入本机 compose：
#   - /opt/synwevo/config/clamd.conf:/etc/clamav/clamd.conf:ro
docker compose config --quiet
docker compose up -d --no-deps --force-recreate --wait clamav
docker compose exec -T clamav clamconf -n </dev/null | grep -i alertencrypted   # AlertEncryptedArchive = "yes"
```

回滚：去掉那一行挂载并 `up -d --no-deps --force-recreate --wait clamav`；应用对顶层加密文件的识别不依赖这个选项。
重建 clamav 期间新上传按原规则等待（来源停在隔离态自动重试，模板/附件/成员导入回 503），不影响已接纳的文件。

自 CI-25 起账户服务也接收上传（组织成员批量导入）。仓库 compose 已给 `accounts` 加上同样的 `SYNWEVO_CLAMD_ADDRESS`/`SYNWEVO_MALWARE_SCAN=required`
与 `depends_on: clamav`；接收器不同步 compose，运维把这两项与依赖合并进 `/opt/synwevo/config` 的 compose 后 `up -d accounts` 即生效。
未配置时导入页面显示「安全检查：此实例未配置扫描引擎」，`/readyz` 的 `malware_scanner` 组件在账户角色同样报告状态。

### 8.1 补扫扫描上线前存入的文件（CI-90）

策略、结论与复核流程在 `docs/security/file-acceptance.md` §8.1。生产上在各自的容器里运行（用容器自己的扫描配置），
stdin 接 `/dev/null`（`exec -T` 没有终端；不接的话命令可能等输入）：

```bash
cd /opt/synwevo/config
# 1. 先预览（不加 --apply 就只预览）：各状态的行数、不同内容份数、超过扫描上限的份数，以及扫描器是否可用
docker compose -f /opt/synwevo/config/compose.yaml exec -T docs python -m synwevo_docs.cli rescan </dev/null
docker compose -f /opt/synwevo/config/compose.yaml exec -T messages python -m synwevo_messages.cli rescan </dev/null
# 2. 分批补扫（可重复运行：已干净或已拒绝的行不再被选中，出错的下次重试；感染会拒绝行、移走字节、通知上传者）
docker compose -f /opt/synwevo/config/compose.yaml exec -T docs python -m synwevo_docs.cli rescan --apply --limit 500 </dev/null
docker compose -f /opt/synwevo/config/compose.yaml exec -T messages python -m synwevo_messages.cli rescan --apply --limit 500 </dev/null
# 3. 有感染时：看隔离区，复核后恢复（误报且签名已撤回）或永久清除（messages 容器里是同样的三条，改用 synwevo_messages.cli）
docker compose -f /opt/synwevo/config/compose.yaml exec -T docs python -m synwevo_docs.cli quarantine list </dev/null
docker compose -f /opt/synwevo/config/compose.yaml exec -T docs python -m synwevo_docs.cli quarantine release <sha256> </dev/null
docker compose -f /opt/synwevo/config/compose.yaml exec -T docs python -m synwevo_docs.cli quarantine purge <sha256> --yes </dev/null
```

| 项 | 说明 |
|---|---|
| 退出码 | 0 全部干净或无事可做；1 有感染、扫描出错、摘要不符或内容缺失（看摘要），或恢复时一行都没恢复；2 用法/配置错误，或另一个补扫、恢复、清除正在运行（两个容器共用一把锁：先等 docs 的补扫结束再运行 messages 的）；3 扫描器未开启或不可达（开始时：什么都没读、没改；运行中：停在那里，已完成的保留） |
| `--limit N` | 数的是扫描次数：已有结论、缺失、超限的不计入；达到后摘要写"还有 M 份留待下次运行" |
| 输出 | 进度行与摘要只含 id、摘要（SHA-256）、大小、签名与计数，不含文件名与内容，可以贴进工单 |
| 两个应用 | 同一份字节可能既是 Docs 来源又是消息附件。摘要里出现"另一个应用仍有 N 行引用这些字节"时，在另一个容器运行它的 `rescan`；恢复也各自 `quarantine release` |
| 隔离区 | `/opt/synwevo/data/quarantine/`（容器内 `/data/quarantine/`）：`<sha256>` 是字节、`<sha256>.json` 是索引、`<sha256>.d/` 是移在一旁的回归用例。不被任何路由提供；**随 `snapshot.py` 备份进归档**，恢复时合并；不要手工移动，恢复只用 `quarantine release`（它先用当前引擎重扫，仍判感染就拒绝），清除只删隔离副本、不删回归用例 |
| 恢复备份之后 | 再运行一次两个 `rescan --apply`：比结论更早的数据库里，行还不知道结论（读取已由索引把关；补扫标记行、把存储副本移走） |
| 误报 | 提交到 `https://www.clamav.net/reports/fp`，等 freshclam 取到撤回签名的库（`scan.engine` 与容器日志里有库版本）后再 `release` |
| 资源 | 每份内容一次 `INSTREAM`；超过 `SYNWEVO_CLAMD_MAX_BYTES` 的不发送，只记 `too_large`。补扫与在线上传共用同一个 clamd，`--limit` 分批即可 |
| 加密的历史文件 | 结论 `encrypted`（设有密码或含有加密内容，无法检查）：不是恶意软件，**访问不变**，只记 `scan.status=encrypted`（`rescan: true`、`access: unchanged`），摘要列出 id，退出码 1；下一次运行不再选中它们。是否撤回、通知上传者或要求去掉密码后重新上传由所有者决定（file-acceptance §8.1）；`quarantine release` 遇到加密内容不恢复（不能判定为干净） |
| 未覆盖 | 成员导入的原文件（账户服务；到期清理开启后连文件删除，见 file-acceptance §8.1）。已生成的产物不扫描，但输入被拒后一律 410（摘要列出用过被拒字节的任务 id） |

## 9. 人机验证（Cloudflare Turnstile）

策略、校验与验收在 `docs/security/human-verification.md`。运维要点：

| 项 | 值 |
|---|---|
| 启用 | `runtime.env` 同时设置 `SYNWEVO_TURNSTILE_SITE_KEY` 与 `SYNWEVO_TURNSTILE_SECRET`（只设一个则 accounts 拒绝启动）；`SYNWEVO_TURNSTILE_HOSTNAMES` 默认 `SYNWEVO_ISSUER` 的主机 |
| 创建 Widget | Cloudflare 仪表盘 → Turnstile → Add widget → 主机名 `accounts.synwevo.com`（域名迁移前为 `accounts.wegreen.ltd`），模式 Managed；由持有 Cloudflare 账户的人完成，密钥不经聊天或提交 |
| 生效 | `docker compose restart accounts`；核对 `GET /api/auth/state` 的 `challenge.site_key`（无 secret）、注册页出现组件、审计 `auth.challenge result=ok` |
| 故障 | siteverify 不可达时注册/找回/验证码登录返回 503 `challenge_unavailable`（`Retry-After: 30`），不放行；已登录业务、未触发风险的口令登录、机器接口不受影响。审计 `auth.challenge result=error` 记录原因 |
| 回滚 | 删除两个变量并重启；页面不再加载 Cloudflare 脚本，CSP 回到默认 |
| 不做 | 不在生产使用测试密钥（测试密钥对任何令牌都通过，且令牌不带 action）；不给机器接口、Webhook、探针返回挑战 |

## 10. 实时表格（`SYNWEVO_SHEET_LIVE`）：开始与结束试用

表格模板的实时逐项协同（[platform/docs-sheet-live.md](platform/docs-sheet-live.md) §9、§15）缺省关闭。开关只有 Docs 读，取值
`off` / `on` / 逗号分隔的**工作空间 id 名单**（限定试用：只有这些工作空间的表格实时协同，其余照旧「保存时合并」）。它写在主机的
**`/opt/synwevo/config/runtime.env`**：这个文件只在主机上，发布和安装审核过的 `compose.yaml` 都不会替换它（写在 `compose.yaml` 里，
下一次安装基础设施改动就会悄悄结束试用，而且不走下面的结束步骤）；`compose.yaml` 不得再设这个变量——`environment` 会盖过
`runtime.env`。开关在房间打开时读取，改了要**优雅重启 Docs**（按 90 秒宽限期停止；不得 `kill`、`-t 0`，见 infra/production/README.md）：

```bash
docker compose -f /opt/synwevo/config/compose.yaml up -d --no-deps --force-recreate --wait docs
```

下面两条命令都是只读的：

```bash
docker compose -f /opt/synwevo/config/compose.yaml exec -T docs python -m synwevo_docs.cli sheet-live </dev/null
docker compose -f /opt/synwevo/config/compose.yaml exec -T docs python -m synwevo_docs.cli sheet-live --workspace <工作空间ID> </dev/null
```

它显示开关读成什么（试用名单里各工作空间的名字；名单里不存在的 ID 单独列出——打错的 ID 会让试用悄悄不生效）和有未保存实时修改的
表格（工作空间、模板、从哪个版本起、最后修改时间与修改者）。`--workspace` 给的 ID 不是工作空间时报错并返回 2，不显示「0」。
**开关读成什么以这个命令为准**：生产的 uvicorn 日志配置不输出 Docs 的 INFO 行，`docker compose logs docs` 里只有名单含不存在的 ID
时的那条警告。

**试用期间**：`docs/quality/deployment.md` 记着试用的开始（开始第 5 步）。其间每次 `docker compose up -d`（一次部署、安装审核过的
`compose.yaml`、任何重启）之后运行一次 `sheet-live`，确认仍读成「试用：<ID>」。若读成别的，试用是没有走结束步骤就停了：在任何人
保存那些表格之前，把 ID 写回 `runtime.env` 并重启 Docs——未保存的表格草稿还在，房间重新打开就接上。

**开始试用**

1. 找到要试用的内部工作空间的 ID。
2. 在 `runtime.env` 加一行 `SYNWEVO_SHEET_LIVE=<工作空间ID>`（多个用逗号分隔），然后运行上面的重启命令。
3. 运行 `sheet-live`：应读成「试用：<ID>（工作空间名）」，且没有「不存在的 ID」。
4. 在该工作空间打开一张表格模板：顶栏显示「实时协同中」；其他工作空间的表格没有实时状态行（保存时合并）。
5. 在 `docs/quality/deployment.md` 记一条：试用开始、工作空间 ID、UTC 时间、正在运行的提交、操作人。

**结束试用 / 回滚**（顺序不能反：先改名单，未保存的实时修改就再也没有房间能保存，那张表的下一次版本变化会删掉它们的草稿）

1. **冻结**：请试用成员在约定的时间点之前保存并关闭各自打开的实时表格，此后不再编辑。仍开着的页面会在第 5 步重启时退出实时协同。
2. `sheet-live --workspace <ID>` 列出有未保存实时修改的表格，逐个处理：请有编辑权限的成员在实时编辑器里「保存模板」；确实不要的，
   由有权限的成员在版本历史里恢复或撤销时选择放弃实时修改，或用机器接口带 `discard_live: true` 保存。处理完再运行一次，应显示
   「未保存的表格  : 0」。
3. 备份（在主机上照抄即可运行；一次性的容器用 docs 的镜像和数据卷，按 §2 的方式——SQLite 备份 API——在线取一致快照，临时文件写在
   备份目录里而不是容器 512 MB 的 `/tmp`；归档含账户、密钥和业务数据，留在主机）：

   ```bash
   B=/opt/synwevo/backups/sheet-live-$(date -u +%Y%m%dT%H%M%SZ)
   install -d -m 0700 -o 1000 -g 1000 "$B"
   docker compose -f /opt/synwevo/config/compose.yaml run --rm --no-deps -T -e TMPDIR=/backup -v "$B":/backup docs \
     python apps/docs/scripts/snapshot.py create --data /data --out /backup </dev/null
   docker compose -f /opt/synwevo/config/compose.yaml run --rm --no-deps -T -e TMPDIR=/backup -v "$B":/backup docs \
     python apps/docs/scripts/snapshot.py verify --archive /backup/<上一步写出的 synwevo-….tar.gz> --journal-from /data </dev/null
   ```

   `verify` 要以「N/N 项通过」结束。
4. **紧接着重启之前**再运行一次 `sheet-live --workspace <ID>`：必须是「未保存的表格  : 0」。不是 0（冻结之后又有人编辑了）就回到第 2 步。
5. 从 `runtime.env` 去掉该 ID（全部结束就设 `off` 或删掉这一行），运行上面的重启命令。
6. 运行 `sheet-live`（不带 `--workspace`：所有工作空间）：该工作空间不应再有未保存的表格。**若有**（第 4 步与重启之间又有人编辑）：
   在任何人保存那张表之前，把 ID 写回 `runtime.env` 并重启 Docs——房间重新读入那份草稿，修改回到页面上——然后从第 1 步重来。
   不要手工删除草稿。
7. 在 `docs/quality/deployment.md` 记下试用结束（UTC 时间、正在运行的提交、第 3 步的备份目录）。

**回滚代码**到不认识表格草稿的版本之前，另按 docs-sheet-live.md §9 末尾清掉表格草稿。

## 11. 付费 Agent（DeepSeek）：停止开关、生产参数与告警处理

适用于平台付费、按用户计费的受治理 DeepSeek 路径。行为与边界见 [platform/model-risk.md](platform/model-risk.md)（单次上限、
刹车、总闸、放行名单、余额与对账）和 [platform/model-traffic.md](platform/model-traffic.md)（速率、在途、排队）。本节只写运维取值与步骤；
下面的数是**首次生产开放的起点**，不是已验证的吞吐量。所有成本策略字段都在 Account 管理端「配置与治理 → 模型成本」页修改：
预览、写理由、发布新版本（版本化、审计），不改主机文件、不重启。

### 11.1 两个停止开关

| | 一级：成本策略 `enabled=false` | 二级：从 `SYNWEVO_SUITE_APPS` 去掉 `agent` |
|---|---|---|
| 操作 | 「模型成本」页取消“启用”，写理由并发布 | 主机 `/opt/synwevo/config/runtime.env` 改这一行，重建 portal 角色（命令见下） |
| 生效 | 下一次付费调用的准入：策略在发送事务里读取，没有缓存，不需要重启（`tests/integration/test_model_cost_settings.py::test_real_dispatch_reads_new_policy_without_restart`） | portal 重建完成后 |
| 用户看到 | 新的付费调用在发送前被拒绝（`billing_cost_admission_disabled`）：“服务暂时不可用，未发送、未扣费” | Agent 不再注册：没有应用卡片、接口与工具 |
| 照常运行 | 已发送调用的收尾与结算、计量恢复、余额监控、每日对账与账本审计 | 账户服务的管理页（总闸、放行名单、对账记录仍可查看与操作） |
| 一起停止的 | 无 | 计量恢复线程与风险定时任务（`RiskMonitor`）随 Agent 停止：期间没有余额读数，对应日子的每日对账为 `incomplete`；用量未知的调用保持未决，直到重新启用 |
| 恢复 | 发布 `enabled=true` 的新版本 | 把 `agent` 写回并重建 portal |

另有一个等效于一级的手动入口：「付费风险控制」页的「关闭全部付费调用」（总闸，写理由；重新开启要理由与管理重认证）。它不产生新的成本策略
版本；上游余额低于停止阈值、上游报告账户不可用或上游返回 402 时它会自动关闭。

**顺序**：先一级（或总闸）；在「模型成本」页的「当前模型调用流量」确认“进行中的调用”为 0；确有需要再用二级。直接二级会让进行中的调用随
进程重启中断，变成“用量未知”，在 Agent 重新启用前不会被处理。二级的主机步骤（先说明再做，先备份）：

```bash
cp /opt/synwevo/config/runtime.env /opt/synwevo/config/runtime.env.bak-$(date -u +%Y%m%dT%H%M%SZ)
# 编辑 runtime.env：SYNWEVO_SUITE_APPS 去掉 agent，其余应用原样保留
docker compose -f /opt/synwevo/config/compose.yaml up -d --no-deps --force-recreate --wait portal </dev/null
```

两种开关的每一次使用都写入 `docs/quality/deployment.md`（UTC 时间、哪个开关、原因、正在运行的提交、操作人）。

### 11.2 生产参数的约束

DeepSeek 不公布 RPM/TPM，只有按模型的账户并发上限：`deepseek-flash` 2500、`deepseek-v4-pro` 500（2026-10-05 读取官方 rate_limit 页；
每次改参数前重新核对）。超出返回 429。平台的在途上限（`max_inflight`）是**两条路线合计**、没有按路线的在途上限，所以全部在途调用可能
落在同一个模型上。由此得出下面的规则（`tests/integration/test_model_risk_guidance.py` 用 11.3 的示例逐条核对）：

| # | 规则 | 不满足时 |
|---|---|---|
| 1 | `max_inflight` ≤ 0.8 × 已启用路线中**最小**的账户并发（启用 v4-pro 时 ≤ 400；只有 flash 时 ≤ 2000） | 上游 429；用量未知的调用在过期前仍计入在途，留 20% 余量 |
| 2 | `max_unknown` ≥ `max_inflight` | 进行中的调用也计入“用量未知”，`max_unknown` 会成为真正的并发上限。此外只有冻结期内（运行截止，最长 1 小时）因超时、断流等原因用量未知的调用计入；HTTP 错误按已知零用量立即结算，用户“停止”的调用在途结束后只计入该付款方（model-risk.md §1），所以留出的余量只需覆盖一小时内的异常断流 |
| 3 | `max_call_cost` ≥ 每条已启用路线在**高峰价**下的单次保守上界：⌈输入上限 × max(命中价, 未命中价) + 输出上限 × 输出价⌉ + 1（网关 `upper_bound`，成本单位） | 该路线的每一次调用都被拒绝（`cost_call_limit_exceeded`） |
| 4 | `unknown_budget` ≥ `max_unknown` × `max_call_cost` | 未知用量金额先于数量上限触顶 |
| 5 | `traffic.payer_tpm` ≥ 每条已启用路线的 输入上限 + 输出上限（发送前的 token 估算） | 每一次调用都被拒绝（`traffic_request_too_large`） |
| 6 | `traffic.payer_inflight` ≤ `max_inflight`，付款人 RPM/TPM ≤ 平台 RPM/TPM，排队上限在总上限内 | 发布时校验拒绝 |
| 7 | `risk.payer_window_cost` ≥ `traffic.payer_inflight` × `max_call_cost`；`risk.route_window_cost` ≥ `max_inflight` × `max_call_cost` | 正常的并发调用就会触发刹车（窗口内未结算的调用按保守上界计） |
| 8 | `risk.balance_close` ≥ `unknown_budget` | 总闸关闭后，已发送的调用仍可能把上游余额扣到 0，触发上游 402 |
| 9 | `risk.balance_warn` ≥ `risk.balance_close` + `daily_budget` | 预警到停止之间不足一天的支出，来不及充值 |
| 10 | `risk.reconcile_tolerance` 起步 1 元，`risk.pending_alert` 约为 `unknown_budget` 的一半 | 容差过小会把定档与断流的合理差异当作漂移（model-risk.md §7） |

每人的额度不在这里设置：单次运行最多 12 次调用、1 小时（代码固定）；每条回答的最高金额默认为当前路线单次调用预留 × 12，本人可在
Agent 设置中调低（`run_budget_cap`），发送时服务端自动签发并绑定运行授权；个人日/周/月上限由本人在首次使用的「开始使用」步骤或
Agent 设置中填写，未设置或已停用个人上限时付费发送在准入时即被拒绝（`billing_payer_budget_not_configured` / `_disabled`）。

### 11.3 首次生产开放（放行名单阶段）的起点

假设：两条路线都启用，输入上限 131072、输出上限 32768 token；成本价用 DeepSeek 人民币价目表的**高峰价**（每百万 token：flash 未命中 2、
输出 8；v4-pro 未命中 9、输出 27）；成本单位 `CNY`、scale 6（1 = 0.000001 元，必须与已发布的成本价单位一致）。单次保守上界：
flash 524,289，v4-pro 2,064,385（约 2.06 元）。

<!-- model-risk-guidance:start -->
```json
{
  "enabled": true,
  "unit": {"code": "CNY", "scale": 6},
  "daily_budget": "200000000",
  "weekly_budget": "1000000000",
  "monthly_budget": "3000000000",
  "unknown_budget": "120000000",
  "max_inflight": 32,
  "max_unknown": 48,
  "max_call_cost": "2100000",
  "traffic": {
    "platform_rpm": 240, "platform_tpm": 10000000,
    "payer_rpm": 20, "payer_tpm": 1000000, "payer_inflight": 2,
    "wait_seconds": 60, "max_waiting": 64, "max_waiting_per_payer": 2
  },
  "risk": {
    "allowlist_only": true,
    "payer_window_s": 3600, "payer_window_cost": "20000000",
    "route_window_s": 3600, "route_window_cost": "100000000",
    "balance_warn": "400000000", "balance_close": "120000000", "balance_poll_s": 300,
    "reconcile_tolerance": "1000000", "pending_alert": "60000000"
  }
}
```
<!-- model-risk-guidance:end -->

即：平台每日 200 元、每周 1000 元、每月 3000 元；在途 32、未知 48 次 / 120 元；单次上限 2.10 元；平台每分钟 240 次、付款人每分钟 20 次、
付款人同时 2 个；付款人每小时 20 元、每条路线每小时 100 元触发刹车；上游余额低于 400 元提醒、低于 120 元自动关闭总闸，每 5 分钟读取；
对账容差 1 元；待确认用量超过 60 元提醒。

**扩大到公开之前**（需求 §5 的上线顺序）：放行名单阶段至少连续数日每日对账为 `ok`、账本审计无差异、没有异常扣费，再发布
`allowlist_only=false` 的新版本，并按所有者确定的预算放大日/周/月上限；在途上限仍受规则 1 约束（启用 v4-pro 时 ≤ 400）。

### 11.4 告警与处理

告警经站内通知与邮件发给持有 `platform.config.manage` 的平台管理员（付款人只收到自己的暂停通知），详情都在「付费风险控制」页，
只有金额、状态与代码，没有对话内容。

| 告警 | 先做什么 |
|---|---|
| 上游余额偏低 | 给 DeepSeek 账户充值；完成后在「付费风险控制」页登记上游充值（金额、时间、理由），否则当日对账会显示漂移 |
| 付费调用总闸已关闭 | 看原因：余额低于停止阈值 / 上游不可用 / 上游 402 / 管理员。充值并等到下一次余额读数高于停止阈值后，写理由重新开启；余额仍低时下一次读数会再次关闭 |
| 付费调用自动刹车 | 在页面看是哪个付款人或路线、窗口与阈值；核实是正常高峰还是异常（泄露的会话、脚本），再决定解除（理由 + 重认证）或保持 |
| 模型成本对账 | `drift`：先核对是否有未登记的上游充值，再看待确认用量；`incomplete`：缺少边界读数（例如 Agent 被二级开关停过），人工核对后在页面重算 |
| 账本余额审计差异 | 不要自动修复；保留现场，按 [billing-ledger.md](billing-ledger.md) 排查，修复是另行决定的维护操作 |
| 用量待确认的成本偏高 | 查看计量恢复线程是否运行、上游是否中断；必要时先用一级开关停止新调用 |
| 支付回调处理失败 | 按支付渠道的说明检查回调配置与收件箱记录 |

## 成员导入到期清理（CI-96）

成员导入的记录与上传的名单文件标有 7 天有效期。账户服务每小时的清扫可以删除到期的记录（从不删除正在执行的导入）与其上传文件，以及没有记录、超过 7 天的旧上传文件。
这会永久删除数据，所以**缺省关闭**，由持有人决定：在主机的 `/opt/synwevo/config/compose.yaml` 给 `accounts` 服务加 `SYNWEVO_MEMBER_IMPORT_CLEANUP: 'on'`，再 `docker compose -f /opt/synwevo/config/compose.yaml up -d accounts`。
隐私页「本部署的当前状态」读取 `/api/auth/state` 的 `member_import_cleanup` 显示开关，不需要改文案。开启前可先在账户容器里数一数将被删除的导入：
`docker compose -f /opt/synwevo/config/compose.yaml exec -T accounts python -c "import time,sqlite3,glob,os; p=glob.glob(os.environ['SYNWEVO_DATA_DIR']+'/*.sqlite3')[0]; c=sqlite3.connect('file:'+p+'?mode=ro',uri=True); print(c.execute(\"SELECT COUNT(*) FROM member_imports WHERE expires_at<? AND status<>'running'\",(time.time(),)).fetchone())" < /dev/null`
