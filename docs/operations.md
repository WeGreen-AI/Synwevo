# 运维手册（部署、备份、升级、回滚）

Vultr Tokyo 三域名部署与 CI/CD 见 [生产部署说明](../infra/production/README.md)，实际执行状态见
[部署记录](quality/deployment.md)。下面保留通用单进程与本机运行方式。

Synwevo Docs 是**单进程服务**：Web 界面、机器接口、任务运行线程与 Webhook 投递线程在同一进程内，状态放在
`SYNWEVO_DATA_DIR`（SQLite + 内容寻址文件存储）。没有额外的队列或对象存储组件。端口见
[integration/ports.md](integration/ports.md)，配置项见仓库根目录 `.env.example`。

## 当前本机产品轮：开放注册与隔离邮件

2026-09-25 本轮不操作生产。`main` 推送会触发既有生产工作流，所以本轮成果暂不推送。
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
| 来源文件停在「安全检查暂不可用」（`scan_failed`）、`/readyz` 的 `malware_scanner` 为 `down: …` | clamd 不可达：`docker compose logs clamav`（首次启动要先下载病毒库，`start_period` 15 分钟）；恢复后文件按 30 s→10 min 的间隔自动重试，不需要重新上传。文件超过扫描上限（`scan.status=too_large`）则调大 `SYNWEVO_CLAMD_MAX_BYTES` 与 clamd 的 `StreamMaxLength` 后让用户重新上传 |
| 上传返回 400 `malware_detected`、文件显示「安全检查未通过」 | 引擎命中签名（`scan.signature`、`scan.engine` 记录了判定时的引擎与病毒库）。字节已从 blob 存储删除；没有"仍然处理"的入口。怀疑误报按 docs/security/file-acceptance.md §8 处理 |
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

2026-09-26 核对的发信域状态（`hikosphere.com`）：SPF `v=spf1 ip4:149.28.18.222 include:_spf.mx.cloudflare.net ~all`
**没有** SMTP2GO 出口；DKIM `mailcow2026._domainkey` 已发布且与 rspamd 持有的公钥一致；DMARC `p=none`。
`wegreen.ltd` 也已有 mailcow 签发并发布的 `mailcow2026` DKIM。要提高机构网关的接受率，按顺序做（都是 DNS/供应商侧，不改代码）：

1. SMTP2GO 后台验证发件域 `hikosphere.com`（按其给出的 CNAME 记录发布），这样回信地址（Return-Path）与 DKIM 都与 From 对齐，
   退信也会回到可查的地方；同时给事务邮件关闭「链接跟踪 / 打开跟踪」，否则 SMTP2GO 重写正文会破坏 mailcow 的签名，机构网关还会再重写一次链接。
2. SPF 加 `include:spf.smtp2go.com`（若 SMTP2GO 使用自己的回信域，这一条不影响 DMARC，但让 From 域的 SPF 记录与实际出口一致）。
3. DMARC 先保持 `p=none` 并把 `rua` 指向能看到的报表；对齐验证一周无失败后再考虑 `quarantine`。
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

自 CI-25 起账户服务也接收上传（组织成员批量导入）。仓库 compose 已给 `accounts` 加上同样的 `SYNWEVO_CLAMD_ADDRESS`/`SYNWEVO_MALWARE_SCAN=required`
与 `depends_on: clamav`；接收器不同步 compose，运维把这两项与依赖合并进 `/opt/synwevo/config` 的 compose 后 `up -d accounts` 即生效。
未配置时导入页面显示「安全检查：此实例未配置扫描引擎」，`/readyz` 的 `malware_scanner` 组件在账户角色同样报告状态。

## 9. 人机验证（Cloudflare Turnstile）

策略、校验与验收在 `docs/security/human-verification.md`。运维要点：

| 项 | 值 |
|---|---|
| 启用 | `runtime.env` 同时设置 `SYNWEVO_TURNSTILE_SITE_KEY` 与 `SYNWEVO_TURNSTILE_SECRET`（只设一个则 accounts 拒绝启动）；`SYNWEVO_TURNSTILE_HOSTNAMES` 默认 `SYNWEVO_ISSUER` 的主机 |
| 创建 Widget | Cloudflare 仪表盘 → Turnstile → Add widget → 主机名 `accounts.wegreen.ltd`，模式 Managed；由持有 Cloudflare 账户的人完成，密钥不经聊天或提交 |
| 生效 | `docker compose restart accounts`；核对 `GET /api/auth/state` 的 `challenge.site_key`（无 secret）、注册页出现组件、审计 `auth.challenge result=ok` |
| 故障 | siteverify 不可达时注册/找回/验证码登录返回 503 `challenge_unavailable`（`Retry-After: 30`），不放行；已登录业务、未触发风险的口令登录、机器接口不受影响。审计 `auth.challenge result=error` 记录原因 |
| 回滚 | 删除两个变量并重启；页面不再加载 Cloudflare 脚本，CSP 回到默认 |
| 不做 | 不在生产使用测试密钥（测试密钥对任何令牌都通过，且令牌不带 action）；不给机器接口、Webhook、探针返回挑战 |

## 成员导入到期清理（CI-96）

成员导入的记录与上传的名单文件标有 7 天有效期。账户服务每小时的清扫可以删除到期的记录（从不删除正在执行的导入）与其上传文件，以及没有记录、超过 7 天的旧上传文件。
这会永久删除数据，所以**缺省关闭**，由持有人决定：在主机的 `/opt/synwevo/config/compose.yaml` 给 `accounts` 服务加 `SYNWEVO_MEMBER_IMPORT_CLEANUP: 'on'`，再 `docker compose -f /opt/synwevo/config/compose.yaml up -d accounts`。
隐私页「本部署的当前状态」读取 `/api/auth/state` 的 `member_import_cleanup` 显示开关，不需要改文案。开启前可先在账户容器里数一数将被删除的导入：
`docker compose -f /opt/synwevo/config/compose.yaml exec -T accounts python -c "import time,sqlite3,glob,os; p=glob.glob(os.environ['SYNWEVO_DATA_DIR']+'/*.sqlite3')[0]; c=sqlite3.connect('file:'+p+'?mode=ro',uri=True); print(c.execute(\"SELECT COUNT(*) FROM member_imports WHERE expires_at<? AND status<>'running'\",(time.time(),)).fetchone())" < /dev/null`
