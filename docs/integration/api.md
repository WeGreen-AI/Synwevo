# 机器接口（`/api/v1`）

与界面**共用同一套引擎、服务与任务系统**：同样的输入、模板版本和规则，通过界面或接口得到同样的业务结果。
接口不依赖浏览器、页面、DOM 或鼠标选区。产品界面中不展示这些能力，密钥与回调通过命令行和部署配置管理。

* 基础地址：`http://<host>:<port>/api/v1`（默认 `http://127.0.0.1:8765/api/v1`，端口见 [ports.md](ports.md)）
* 机器可读规范：[openapi.yaml](openapi.yaml)（由 `python backend/manage.py openapi` 从实际路由导出，`backend/tests/test_api_docs.py` 校验其与代码一致）
* 调用示例与脚本：[automation.md](automation.md)

## 1. 鉴权

```
Authorization: Bearer dw_<key_id>_<secret>
```

```bash
python backend/manage.py apikey create --name ci --tenant default --scopes read,write
python backend/manage.py apikey list
python backend/manage.py apikey rotate <key_id>     # 生成新密钥并撤销旧的
python backend/manage.py apikey revoke <key_id>
```

* 密钥只在创建时显示一次，服务器只保存 SHA-256 哈希；**不要**写进前端包、仓库或日志。
* `--scopes`：`read`（所有 GET）、`write`（创建 / 修改 / 提交任务）、`admin`（删除项目、模板）。权限不足返回 `403 forbidden`。
* `--tenant`：资源归属。`default` 与界面共用（界面租户由 `DOCWEAVE_UI_TENANT` 决定，默认 `default`），其它租户的数据互不可见：访问他人资源一律返回 `404`，不泄露是否存在。
* `--expires-days`：可选有效期；过期返回 `401 key_expired`，撤销返回 `401 key_revoked`。
* 限流：默认每个密钥 300 次/分钟（`DOCWEAVE_API_RATE_PER_MIN`，按进程计），超出返回 `429 rate_limited` 并带 `Retry-After`。

## 2. 错误与追踪

所有 `/api/v1` 错误的响应体结构一致，HTTP 状态码表达类别：

```json
{ "error": { "code": "invalid_definition", "message": "第 1 个字段「客户」：模板中找不到文字「示例」",
             "field": "find", "request_id": "9f2c…" } }
```

| 状态 | 场景 |
|---|---|
| 400 | 请求内容不合法（`invalid`、`invalid_definition`、`bad_error_policy`、`format_unavailable`…） |
| 401 / 403 | 密钥缺失/无效/过期/被撤销；权限不足 |
| 404 | 资源不存在或不属于该租户 |
| 409 | 幂等键冲突（`idempotency_conflict`）、映射版本过期（`stale`）、预检发现错误（`has_errors`）、任务状态不允许（`job_active`、`nothing_to_retry`） |
| 410 | 产物已删除 / 链接过期（`artifact_deleted`、`link_expired`） |
| 413 | 超过文件大小上限 |
| 429 | 限流 |
| 500 | 未预期错误（响应含 `request_id`，服务端日志打印同一个 id） |

每个响应都带 `X-Request-ID`；客户端也可自带该头以串联日志。业务校验失败一律用上述状态码，不会伪装成 200。

## 3. 资源与路由

### 3.1 能力与状态

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/capabilities` | read | 当前部署真实可用的格式矩阵、转换器（LibreOffice）、OCR 语言、PDF 引擎与上限 |
| GET | `/whoami` | — | 当前密钥的租户与权限 |
| GET | `/healthz`（根路径） | 无 | 进程存活 |
| GET | `/readyz`（根路径） | 无 | 是否能处理任务：数据库可写、存储可写、任务线程存活；未就绪返回 503 |

### 3.2 项目、来源文件

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/projects` | write | 创建项目 |
| GET | `/projects` | read | 列表（分页：`offset`、`limit`） |
| GET | `/projects/{pid}` | read | 详情：模板绑定、规则、来源文件、映射数 |
| DELETE | `/projects/{pid}` | admin | 删除项目（软删除，产物保留） |
| POST | `/projects/{pid}/files` | write | 上传来源文件：`multipart/form-data` 的 `file` 字段；或 JSON `{"path": "..."}`（仅限 `DOCWEAVE_IMPORT_ROOTS` 下） |
| GET | `/projects/{pid}/files` | read | 来源文件与解析状态 |
| GET | `/files/{fid}` | read | 单个文件：`status`（`parsing`/`ready`/`needs_ocr`/`error`）、`structure`（工作表、页数、表头等）、`sha256` |
| DELETE | `/files/{fid}` | write | 从项目移除 |

上传后解析是异步的：轮询 `/files/{fid}` 直到 `status != "parsing"`。

### 3.3 模板与字段定义

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/templates` | write | 上传模板（DOCX/XLSX/PDF/PPTX/PNG/JPEG，旧格式自动转换并提示） |
| GET | `/templates` | read | 模板列表 |
| GET | `/templates/{tid}` | read | 详情：字段、版本历史、可用导出格式 |
| GET | `/templates/{tid}/fields` | read | 导出字段定义（`docweave.template-fields` v1） |
| POST | `/templates/{tid}/fields` | write | 按定义创建字段（每次调用产生一个新模板版本） |
| GET | `/templates/{tid}/content?which=original\|working` | read | 下载原始模板或含字段的工作副本 |
| DELETE | `/templates/{tid}` | admin | 删除模板 |

字段定义（可手写，也可从界面制作后导出复用）：

```json
{ "schema": "docweave.template-fields", "version": 1,
  "fields": [
    { "kind": "text",   "name": "客户名称", "find": "示例客户名称", "occurrence": 1 },
    { "kind": "cell",   "name": "订单号",   "sheet": "订单", "cell": "B2" },
    { "kind": "repeat", "name": "明细",     "sheet": "订单", "row": 4, "c1": 0, "c2": 3 },
    { "kind": "repeat_block", "name": "明细项", "first_find": "行号：1", "last_find": "单价（元）：0.00" },
    { "kind": "pdf_box", "name": "单号", "page": 0, "find": "DD-示例", "align": "left" },
    { "kind": "pdf_repeat", "name": "明细", "page": 0, "rect": [72, 172, 523, 304], "overflow": "continue" }
  ] }
```

支持的 `kind` 与可用键（未知键、未知版本一律拒绝并指出位置）：

| kind | 适用模板 | 选择方式 |
|---|---|---|
| `text` | DOCX / PPTX | `find`(+`occurrence`,`part`) 或 `path`+`start`+`end`（PPTX 用 `slide`/`shape`/`para`/`start`/`end`）；`mode`: `replace`/`insert` |
| `cell` | XLSX / DOCX 表格 / PPTX 表格 | XLSX：`sheet`+`cell`；DOCX：`table`(第几个表格)+`row`+`col`；PPTX：`slide`+`shape`+`row`+`col` |
| `region` | XLSX / DOCX 表格 | XLSX：`sheet`+`range`；DOCX：`table`+`r1`,`c1`,`r2`,`c2` |
| `repeat` | XLSX / DOCX / PPTX | 明细模板行：XLSX `sheet`+`row`+`c1`+`c2`；DOCX `table`+`row`(+`header_rows`)；PPTX `slide`+`shape`+`row`；可带 `empty_policy`、`placeholder` |
| `repeat_block` | DOCX | 连续段落块：`first_find`/`last_find` 或 `first`/`last` 路径；`auto_columns`、`separator`(`none`/`blank`/`page_break`)、`empty_policy` |
| `block_column` | DOCX | 给已存在的内容块加一列：`block`(名称或 id) + `find` 或 `path`+`start`+`end` |
| `pdf_box` | PDF / 图片 | `page`+`rect`（页面坐标，单位点）、`nrect`（0–1 比例）或 `find`；`font_size`、`min_font_size`、`align`、`valign`、`color`、`clear` |
| `pdf_repeat` | PDF / 图片 | 同上定位 + `layout`（列与行高，可省略由服务识别）、`overflow`(`error`/`continue`/`shrink`)、`empty_policy` |
| `pdf_field` | PDF 表单 | `field`（AcroForm 字段名） |

界面能做而 API 暂不支持的：内置编辑器里新建模板（`builtin_doc` / `builtin_sheet`）只能在界面创建，API 可读取、绑定与生成。

### 3.4 规则、映射与项目定义

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| PUT | `/projects/{pid}/rule` | write | 保存样本规则，并返回按当前来源文件识别到的样本数与文件级错误 |
| GET/PUT | `/projects/{pid}/mappings` | read/write | 读取、保存映射；PUT 可带 `base_rev` 做并发校验（不一致返回 409 `stale`） |
| PUT | `/projects/{pid}/template` | write | 绑定模板；可带 `version` 固定模板版本 |
| GET/PUT | `/projects/{pid}/definition` | read/write | 项目定义（`docweave.project` v1）：模板绑定 + 规则 + 映射，便于从一个项目复用到另一个 |

样本规则（与界面一致）：

```json
{ "mode": "per_row",   "table": { "sheet": "客户", "header_row": 0, "data_start": null, "data_end": 100,
                                   "skip_blank": true, "filters": [{"column": "状态", "op": "eq", "value": "有效"}] } }
{ "mode": "per_file" }
{ "mode": "per_group", "table": {...}, "group": { "keys": [{"header": "订单号"}],
                                                   "sort": [{"header": "行号", "dir": "asc", "numeric": true}], "scope": "file" } }
{ "mode": "per_block", "block": { "anchor": "人员登记表", "match": "contains", "end_anchor": null } }
```

映射：`{"type": "single|region|repeat", "sources": [<定位器>], "target": {"binding": "<字段 binding>"}, "transforms": [...], "columns": [...]}`。
常用定位器：`{"kind":"column","sheet":"客户","header":"姓名"}`、`{"kind":"cell","sheet":"订单","cell":"B2"}`、
`{"kind":"table","sheet":"明细","header_row":0}`、`{"kind":"docx_text","part":..,"path":..,"prefix":..}`、
`{"kind":"pdf_region","page":0,"rect":[...],"anchor":{...}}`、`{"kind":"constant","value":"..."}`。

### 3.5 预检与任务

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/projects/{pid}/preflight` | read | 样本数、有效数、错误数、警告数、预计输出文件数、命名示例、静态校验问题 |
| POST | `/projects/{pid}/jobs` | write | 提交任务（202）。头 `Idempotency-Key` 或 body `idempotency_key` |
| GET | `/projects/{pid}/jobs` | read | 项目的任务列表 |
| GET | `/jobs/{jid}` | read | 状态与计数 |
| GET | `/jobs/{jid}/items` | read | 逐样本结果，`status` 过滤、`offset`/`limit` 分页 |
| GET | `/jobs/{jid}/runs` | read | 运行记录（初次生成与每次重试） |
| GET | `/jobs/{jid}/events?after=<seq>` | read | 事件流（轮询），`next_after` 用于下一次请求 |
| POST | `/jobs/{jid}/cancel` | write | 取消 |
| POST | `/jobs/{jid}/retry` | write | 重试失败/取消项（沿用原快照），支持 `Idempotency-Key`、`include_skipped` |

任务提交体：`{"formats": ["docx","pdf"], "naming": "{客户编号}_{客户名称}", "error_policy": "block|skip", "layout": "off|warn|fit", "keep_fields": false}`。

**状态机**：`queued → running → (completed | completed_with_errors | failed | cancelled)`；取消请求把运行中的任务置为 `cancelling`。

| 状态 | 含义 | 可执行操作 |
|---|---|---|
| `queued` | 已入队，尚未开始 | 取消 |
| `running` | 正在逐样本生成 | 取消 |
| `cancelling` | 已接受取消请求：不再开始新样本，正在处理的样本会完成并保留结果 | 查询 |
| `completed` | 全部样本成功 | 重试（无失败项时返回 409） |
| `completed_with_errors` | 部分成功 | 重试失败项 |
| `failed` | 无任何成功样本，或任务级错误（`error` 字段说明） | 重试 |
| `cancelled` | 有样本被取消未执行；已完成样本的结果保留 | 重试被取消项 |

`counts`：`samples`（样本总数）、`documents`（成功样本数）、`files`（产物文件数 = 成功样本 × 输出格式数）、`failed`、`skipped`、`cancelled`、`pending`、`running`。

**幂等**：同一租户 + 同一路径作用域 + 同一 `Idempotency-Key`：
内容相同 → 返回同一个任务并带 `"idempotent_replay": true`；内容不同 → `409 idempotency_conflict`。
键的保留期默认 24 小时（`DOCWEAVE_IDEMPOTENCY_TTL_HOURS`），过期后同一个键会创建新任务。并发提交由数据库唯一约束裁决，只会产生一个任务。

**快照**：任务创建时固定来源文件版本、模板版本、字段、映射、规则与生成参数；之后在界面或 API 修改任何内容都不影响已提交的任务。

**重试**：默认沿用原任务快照，作为该任务的新一次「运行」记录（`/runs`），不会覆盖已成功的产物。若输入、模板或规则已变化，请重新预检并提交新任务（新任务与旧任务各自独立）。

**恢复**：任务由工作线程以租约（默认 30 秒，`DOCWEAVE_JOB_LEASE_S`）持有并定期续约。进程被杀、线程异常退出后，租约过期（同机进程已消失时立即判定）会自动把未完成样本放回队列继续；已发布的产物不会重复生成。不承诺「恰好一次执行」，但产物发布是原子的（产物与样本状态同一事务提交）。

### 3.6 结果与下载

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/jobs/{jid}/artifacts` | read | 产物清单：`filename`、`format`、`size`、`sha256`、所属样本 |
| GET | `/artifacts/{aid}/content` | read | 下载单个产物（`Content-Disposition`、正确 MIME、`X-Content-SHA256`） |
| POST | `/artifacts/{aid}/link` | read | 生成有时效的下载链接（默认 15 分钟，`DOCWEAVE_DOWNLOAD_LINK_TTL_S`） |
| GET | `/jobs/{jid}/archive?format=docx` | read | 全部产物 ZIP（含「生成清单.csv」）；服务端写临时文件后流式返回 |
| POST | `/jobs/{jid}/archive-link` | read | ZIP 的有时效链接 |
| GET | `/jobs/{jid}/report.csv` | read | 生成清单（逐样本状态、输出文件、来源位置、问题说明） |
| GET | `/download/{token}` | 链接本身即凭据 | 上述链接的下载入口；过期返回 `410 link_expired`，重新调用 `/link` 即可 |

产物被删除后返回 `410 artifact_deleted`；产物文件内容按 SHA-256 内容寻址存储，可用返回的 `sha256` 校验下载完整性。

## 4. 事件与 Webhook

事件结构（`/jobs/{jid}/events` 与 Webhook 共用）：

```json
{ "seq": 12, "id": "ev_…", "type": "job.completed", "version": 1, "job_id": "j_…",
  "occurred_at": 1758592000.12, "data": { "status": "completed", "run_no": 1, "counts": {...} } }
```

类型：`job.created`、`job.started`、`job.cancel_requested`、`job.retry_requested`、`job.recovered`、
`job.completed`、`job.completed_with_errors`、`job.failed`、`job.cancelled`。`seq` 在同一任务内单调递增（轮询用 `after`）。

Webhook 默认关闭，按部署配置开启：

```bash
DOCWEAVE_WEBHOOK_URLS=https://automation.example.com/hooks/docweave
DOCWEAVE_WEBHOOK_SECRET=<共享密钥>
DOCWEAVE_WEBHOOK_EVENTS=job.completed,job.completed_with_errors,job.failed,job.cancelled
# 仅在确需回调内网时：DOCWEAVE_WEBHOOK_ALLOW_PRIVATE=10.0.0.0/8,automation.internal
```

* 请求头：`X-DocWeave-Event`、`X-DocWeave-Event-Id`、`X-DocWeave-Delivery`、`X-DocWeave-Timestamp`、
  `X-DocWeave-Signature: v1=<hmac_sha256(secret, "<timestamp>.<body>")>`。
* 接收端应：校验签名；拒绝时间戳偏差超过 5 分钟的请求；按 `X-DocWeave-Event-Id` 去重（投递可能重复，顺序不保证，请以 `seq` 或任务当前状态为准）。
* 重试：网络错误、429、5xx 按 30s → 2m → 10m → 30m → 2h 退避，最多 6 次；4xx（除 429）不再重试。投递记录保存在 `webhook_deliveries` 表。
* 安全：默认要求 https；回调地址每次投递前解析，解析到内网地址时拒绝，除非在 `DOCWEAVE_WEBHOOK_ALLOW_PRIVATE` 白名单中；不跟随跳转。
* 回调失败不影响产物查询与下载。

## 5. 限制与约定

* 分页：`offset` + `limit`，响应含 `total`、`next_offset`（`null` 表示到底）。
* 文件大小上限 `DOCWEAVE_MAX_FILE_MB`（默认 100 MB）；单任务样本上限 `DOCWEAVE_MAX_RECORDS_PER_JOB`（默认 20 000）。
* 数据保留：产物与来源文件保存在 `DOCWEAVE_DATA_DIR`，不自动过期；清理策略见 [../operations.md](../operations.md)。
* 兼容性：`/api/v1` 路径与字段只增不删；新增字段可能随时出现，客户端应忽略未知字段。定义文档（`docweave.*`）带 `version`，不兼容版本会被拒绝并说明。界面内部接口（不含 `/v1`）不属于兼容承诺范围。
