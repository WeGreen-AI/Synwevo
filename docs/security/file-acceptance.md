# 文件接纳链：先隔离、检查，再进入业务处理

> 状态：2026-09-25 实现（CI-20）。覆盖 Synwevo Docs 的全部文件入口；账户服务的头像走另一条已存在的
> 净化路径（重编码为 PNG，见 §6）。本文写的是**当前代码的行为**，"未做"的部分明确列在 §9。

## 0. 出发点：现状有什么、没有什么（实现前）

| 已有控制 | 位置 | 缺口 |
|---|---|---|
| 上传字节上限（默认 100 MiB，`SYNWEVO_DOCS_MAX_FILE_MB`） | `api/app.py:_read_limited`、`services.add_source` | 只限制字节，不看内容 |
| 结构检查：真实格式识别、ZIP 解压比与条目数、页数/单元格/像素预算 | `engine/detect.py`、`engine/limits.py` | 拒绝炸弹与伪装类型，但不识别恶意软件 |
| LibreOffice 转换在独立进程、受限目录与超时内运行 | `engine/convert.py` | 解析器仍会打开任何通过结构检查的文件 |
| 头像重编码为固定尺寸 PNG（丢弃原始容器与元数据） | `synwevo_identity`：`canonical_avatar` | 已经是内容净化（CDR），本轮不改 |
| 下载路径有对象授权、`no-store`、`X-Content-SHA256` | `api/app.py:source_download`、`v1_sources.content` | 没有"是否已接纳"的判断 |

没有：恶意软件扫描引擎、"隔离中/已拒绝"的状态、对同一字节的二次拒绝、扫描器故障时的策略。

## 1. 决策记录

| 项目 | 结论 |
|---|---|
| 问题与基线 | 任何人（网页、API/SDK、分片上传、服务器路径导入）上传的字节都会立刻被解析器打开并可下载。结构检查能拒绝炸弹，不能识别已知恶意软件；而 Docs 的用户会把来源文件转发给同事。 |
| 成熟参考 | OWASP File Upload Cheat Sheet（cheatsheetseries.owasp.org，读取 2026-09-25）："Run the file through an antivirus or a sandbox if available"、"store on a different server / not in webroot / generated filename"；ClamAV 官方文档 clamd 协议（docs.clamav.net → Manual → Usage → Scanning → clamd，读取 2026-09-25）：`INSTREAM` 分块流式扫描、`StreamMaxLength`、`PING`/`VERSION`；EICAR 标准测试文件（eicar.org，读取 2026-09-25）：68 字节 ASCII，每个合规引擎都以 `Eicar-Test-Signature` 类签名命中，用于验证检测链路而不需要真病毒。 |
| 对比与选择 | (a) ClamAV 本地守护进程：GPLv2、独立容器、无外部数据外流、签名库每小时自动更新，官方镜像 `clamav/clamav`；(b) VirusTotal 等外部平台：会把用户文档发送给第三方——**未授权，不采用**；(c) 云厂商托管扫描：项目当前只有 Cloudflare 边缘与一台 VPS，没有对象存储，不适用。选 (a)。协议自己实现（`malware.py`，约 180 行），不引入 `clamd` Python 包：需要的只有 4 个命令，而现有的包要么不维护要么带着不需要的依赖。 |
| 预期变化 | 来源文件状态多出 `scanning`→`parsing` 与两个终态 `rejected`/`scan_failed`；模板上传变成同步扫描；下载/内容接口对未接纳文件返回 409/410；`scan` 对象随文件与模板返回；`/readyz` 多一个 `malware_scanner` 组件；SDK 的等待函数把 `scanning`/`scan_failed` 视为"仍在等"。 |
| 边界与风险 | 引擎只识别**已知**恶意软件，"clean" 是"未检出已知威胁"；EICAR 只在文件（或压缩包成员）偏移 0 处是签名，嵌在 CSV 中间就是普通文本——测试样本必须按标准放置；超过 `StreamMaxLength` 的文件不扫描也不接纳（默认 25 MiB，可与上传上限一起调大）；ClamAV 常驻约 1.3–1.5 GiB 内存，生产 VPS 需要先确认余量；amd64 官方镜像在 Apple Silicon 上只能以模拟方式运行（仅影响开发机）。 |
| 验收 | §7 的九项：EICAR 被拒绝并隔离、同字节再上传直接拒绝、干净文件照常解析、引擎离线时文件停在隔离态并在恢复后自动接纳、超限文件不放行、模板 400/503/413、下载/内容接口对隔离文件 409、对拒绝文件 410、未配置扫描器时状态是 `not_configured` 而不是"干净"。 |
| 发布与恢复 | schema v7→v8 只加两列（`source_files.scan`、`templates.scan`），旧行读作 `unscanned`；应用先于扫描器上线时 `scan.status=not_configured`，行为与旧版一致；开启 `SYNWEVO_CLAMD_ADDRESS` 后新文件走扫描，历史文件按 §8 补扫。回滚：去掉环境变量即恢复旧行为，列保留。 |

## 2. 入口覆盖：所有字节汇聚到一个函数

| 入口 | 路径 | 汇聚点 |
|---|---|---|
| 工作台多文件上传 | `POST /api/projects/{pid}/sources` | `App.add_source` |
| 机器 API 单文件上传 | `POST /api/v1/projects/{pid}/files`（multipart） | `App.add_source` |
| 机器 API 服务器路径导入 | `POST /api/v1/projects/{pid}/files`（JSON `path`，仅 `SYNWEVO_DOCS_IMPORT_ROOTS`） | `App.add_source` |
| 分片/断点续传 | `POST /api/v1/uploads` … `/complete`（校验 sha256 后） | `App.add_source` |
| 模板上传 | `POST /api/templates/upload`、`POST /api/v1/templates` | `App.upload_template` → `scan_template_bytes` |
| 头像 | 账户服务 `PUT /api/auth/avatar` | `canonical_avatar` 重编码（§6） |

`add_source` 在写入任何行之前先查：这些字节的摘要是否已有一条 `rejected` 记录——有就 400
`malware_detected`，不再落库、不再扫描。写入时行状态为 `scanning`，`scan={"status":"pending"}`，
再把 `_scan_then_parse` 交给解析线程池。没有配置扫描器时状态直接是 `parsing`，
`scan={"status":"not_configured"}`——**缺失是可见的，不是静默的**。

## 3. 状态机

```
                 ┌──────────── clean ────────────┐
upload ─▶ scanning ─┼─ infected ─▶ rejected（字节已删除）  ▼
                 └─ error / too_large ─▶ scan_failed ─┐   parsing ─▶ ready | needs_ocr | error | unsupported
                          ▲   自动重试 30s→60s→2m→5m→10m │
                          └────────────────────────────┘（too_large 不重试：换更大的上限或拆分文件）
```

* `scanning`、`scan_failed`、`rejected` 的行不解析、不预览、不映射、不下载：`_parse_source` 对这三个状态直接返回；
  `require_accepted` 在两个下载路径上分别回 409 `not_yet_accepted`（隔离中）与 410 `rejected_malware`（已拒绝）。
* 结论绑定内容：`scan.sha256` 与行的 `blob` 相同才发布结果；`_publish_scan` 在写回前重新读行，版本或 blob 变了就丢弃结论。
* 引擎与病毒库版本写在 `scan.engine`（`ClamAV 1.4.x/<db 版本>/<db 日期>`），审计时能回答"当时用什么判的"。
* `rejected` 时 `_quarantine_blob`：同一 blob 的所有未拒绝行一起标记 `rejected`（同样的字节、同样的结论），然后
  `unlink` blob 文件——不保留恶意字节，也没有任何"仍然处理"的按钮。
* 模板是同步的：`scan_template_bytes` 在 `upload_template` 的第一行运行，命中 400 `malware_detected`
  （`detail.signature`），引擎不可用 503 `scan_unavailable`（`detail.retry_after=30`），超限 413 `scan_limit`。
  模板不能"隔离后异步接纳"，因为上传模板的下一步就是打开它做槽位分析。

## 4. 引擎适配：`apps/docs/synwevo_docs/malware.py`

* 每个命令一条连接；`zINSTREAM\0` + 4 字节大端长度前缀的 64 KiB 分块 + 零长度块；回复 `stream: OK` /
  `stream: <签名> FOUND` / `... ERROR`。`INSTREAM size limit exceeded` 映射为 `too_large`，其它 ERROR 与
  连接失败/超时抛 `ScanUnavailable`——**没有"默认干净"的分支**。
* 超过 `SYNWEVO_CLAMD_MAX_BYTES` 的数据根本不发送（守护进程会在中途拒绝，白白传一遍）。
* `policy()` 拒绝矛盾配置：`SYNWEVO_MALWARE_SCAN=required` 却没有地址 → 启动失败，而不是静默变成不扫描。
* `health()` 供 `/readyz`：`ok` / `off` / `down: <原因>`。扫描器不可达**不会**让 Docs 变为未就绪（compose 的
  healthcheck 会因此重启容器，而重启对已接纳的文件毫无帮助）；它是"可降级组件"，在 `checks` 里可见。

## 5. 隔离的解析与转换

接纳只是第一道门。接纳之后的解析仍然按既有隔离运行：LibreOffice 独立进程、受限工作目录、超时与内存预算；
XLSX/DOCX 只读结构，不执行宏或 DDE（openpyxl/python-docx 不实现它们，模板填充也不复制 `vbaProject.bin`）；
PDF 由 pypdfium2 渲染，不执行 JavaScript；图片在解码前先检查像素预算。这些在 `engine/limits.py` 与
`docs/security/permissions.md` §7 已有记录，本轮没有改动。

## 6. 头像与 SVG/HTML 预览

头像在账户服务里被解码并重编码为固定尺寸 PNG（`canonical_avatar`）：原始容器、元数据与任何脚本都不会到达浏览器。
Docs 的预览只输出服务端渲染的 PNG（页面图）与文本，不回放上传的 SVG/HTML；因此本轮不给头像加病毒扫描——
重编码本身就是比签名扫描更强的净化，而扫描一张 256×256 的 PNG 不会带来新的保护。

## 7. 验收证据

### 7.1 协议替身（`apps/docs/tests/test_malware_scan.py`，8 项，本机通过）

替身是一个真正监听 TCP 的 clamd 协议实现：按 EICAR 标记判定、可被"关掉"、可设流上限。它证明的是集成——
分块框架、结论处理、隔离、重试、下载门——不是引擎本身。

| 项 | 断言 |
|---|---|
| 适配器 | 多块流式、`OK`/`FOUND`/`size limit` 三种回复、超限不发送、连接失败抛 `ScanUnavailable` |
| 策略 | 无地址→off；有地址→required；`required` 无地址→拒绝启动 |
| 干净来源 | `scanning`→`ready`，`scan.status=clean`，`scan.sha256==sha256`，可下载 |
| EICAR 来源 | `rejected`、签名 `Win.Test.EICAR_HDB-1`、下载 410、blob 已删除、同字节再上传 400 |
| 引擎离线 | `scan_failed`（attempts=1）、下载 409；引擎恢复后自动重试到 `ready`（attempts≥2） |
| 超限 | `scan_failed` + `too_large`，下载 409 |
| 模板 | 干净 200 带 `scan.clean`；EICAR 400 `malware_detected`；引擎离线 503 `scan_unavailable` |
| 未配置 | 来源 `parsing`+`not_configured`；模板 `not_configured` |

### 7.2 真实引擎（本机 Docker，`clamav/clamav` 官方镜像）

`docs/quality/verification.md` §16.20：ClamAV 1.5.4（签名库 main 63 / daily 28134，3 628 083 条）——EICAR 裸文件与
ZIP 内的 EICAR 命中 `Eicar-Test-Signature`，三种仓库夹具 `OK`，守护进程的 `INSTREAM size limit exceeded` 被解析为
`too_large`；HTTP 链上干净文件 `scanning`→`ready`、EICAR `rejected`（下载 410、blob 已删、再上传 400）、模板 200/400，
`/readyz` 的 `malware_scanner: ok`。常驻内存 950 MiB。EICAR 命中只证明检测链路可用，不证明能拦截所有恶意文件。

## 8. 历史文件与运维

* **补扫**：扫描功能上线前的行 `scan.status=unscanned`。`python -m synwevo_docs.cli rescan --workspace <id>`（TODO，见 §9）
  之前，运维可用 SQL 把目标行置回 `scanning` 并调用 `resume_parsing`；不把 `unscanned` 直接标为感染，也不删历史文件。
* **误报**：ClamAV 误报按官方流程提交（`clamav.net/reports/fp`）；本地没有"跳过扫描"按钮，管理员也没有。
  同一字节被拒后再上传恒为 400——需要重新接纳时先在隔离环境确认签名已撤回，再删除 `rejected` 行。
* **病毒库**：容器内 freshclam 每小时检查；`scan.engine` 记录判定时的库版本；`/readyz` 的 `malware_scanner`
  只说明守护进程可达，不说明库新旧——库日期看 `scan.engine` 或容器日志。
* **资源**：`clamav` 服务 `mem_limit 2048m`（守护进程 + 一次重载）；隔离中的文件占用与普通 blob 相同，
  `scan_failed` 的重试上限是最后一档 10 分钟的无限循环——引擎长期离线时磁盘按上传上限增长，与没有扫描时相同。
* **部署**：`infra/production/compose.yaml` 是参考；接收端不同步 compose，运维需把 `clamav` 服务与 docs 的两个
  环境变量合并进 `/opt/synwevo/config/compose.yaml`，先确认 VPS 有 ≥2 GiB 余量，再 `docker compose up -d clamav`
  等其 healthy，最后重建 docs。回滚只需去掉两个环境变量。

## 9. 明确未做

* `rescan` CLI 与管理界面上的"补扫"按钮：本轮只有 SQL 路径。
* 密码保护的压缩包/Office 文件：ClamAV 报 `OK`（无法解密），结构检查也会放行加密的 XLSX——**这是盲区**，
  当前靠解析失败（`error`）兜底，没有"要求用户提供密码"的流程。
* 产物（生成的 DOCX/PDF/XLSX）不扫描：它们由模板与数据生成，不包含上传的原始字节；若将来允许把上传文件原样
  打包进产物，需要在打包前复用 `scan_template_bytes`。
* 生产尚未部署扫描器（需运维确认内存并合并 compose）。


## 附：成员导入上传（账户服务，CI-25）

组织成员批量导入让账户服务第一次接收用户文件。它走同一条接纳链，只是由账户服务自己执行：

| 环节 | 实现 | 说明 |
|---|---|---|
| 接收 | `POST /api/auth/organizations/{oid}/imports`（multipart，≤ 20 MB，`org.members.import`） | 同一 clamd 策略（`synwevo_core.malware`，已从 Docs 搬入共享包）：感染 400、扫描器不可用 503（不放行）、超限 413；未配置扫描器时 `scan.status=not_configured` 如实返回并显示在页面上 |
| 读取 | `synwevo_docs.ingest.DocumentIngest`（由 `synwevo_server` 注入）或 `synwevo_core.member_import.BasicIngest` | 引擎的全部结构与资源上限照用；导入另加：每表 2000 行、5000 行文本、200 页文字、20 页 OCR、单次 500 行计划 |
| 保留 | 原文件仅在 `extracted/planned` 阶段保存在 `<data>/member_imports/<id>`（0600，7 天），供按需 OCR；执行结束或过期即删除 | 库里只留提取出的行（有界）与逐行结果 |
| 输出 | 报告 CSV 对 `= + - @` 开头的单元格加前缀，`no-store` | 不下载原文件 |

Tesseract 子进程现在与 LibreOffice 一样只继承最小环境（`engine/ocr.py:_child_env`），账户服务的 SMTP 密码不会进入 OCR 子进程。
生产 compose 目前只给 `docs` 容器配置了 `SYNWEVO_CLAMD_ADDRESS`；给 `accounts` 加上同样两项后，导入上传才会真正扫描（见 operations.md §8）。
