# Synwevo 业务应用操作接口（`/api/suite/v1`）

Synwevo Forms、Tables、DataPrep、Extract、Approvals 的机器接口。它不是另一套实现：每个操作与应用页面调用的是同一个
入口（`Capabilities.invoke`），校验、实时角色、运行记录、幂等与事件完全一致；这一层只增加凭据才需要的东西。

* 契约：[`suite-openapi.json`](suite-openapi.json)（OpenAPI 3.1，由**组装后的部署**的运行时注册表生成——五个应用，加上只有
  组装层才登记的跨应用操作；`python -m synwevo_server.suite_contract --out docs/integration/suite-openapi.json` 重新生成，
  `tests/integration/test_suite_machine_contract.py` 会比对文件、导出器与实际路由）。
* 源站：工作空间源站（正式环境 `https://workplace.synwevo.com`）。令牌由账户服务签发（`https://accounts.synwevo.com`）。
* 状态：本地实现并验证，**尚未部署**；正式环境是否开放以 [`../platform/api-coverage.md`](../platform/api-coverage.md) 为准。

## 1. 谁可以调用

只有一种调用方：**本人授权的客户端**（OAuth 2.0 授权码 + PKCE）。每次调用都是“这个客户端，代表这个人，在他授权时选定的
那一个工作空间里”。

| 不接受 | 原因 |
|---|---|
| 浏览器会话 Cookie | 这个前缀不读取会话；页面用 `/api/suite/ops/*` |
| `dw_` 访问密钥 | 属于 Synwevo Docs |
| 服务账户（`client_credentials`） | 这些应用的每个操作都按“人”授权和署名（实时角色、创建人、审批人）。没有人的机器身份需要另一套授权规则，尚未开放；登记时即拒绝（`suite_delegation_only`），令牌也会被拒绝（`service_account_not_supported`） |
| 签发给其他接口的令牌 | 受众必须是 `urn:synwevo:platform:suite`（`invalid_audience`） |

权限是三者的交集，每次调用重新计算：客户端登记的 scope ∩ 本人同意的 scope ∩ **本人此刻**在该工作空间的角色与应用资格。
持有 scope 是必要条件，不是充分条件：查看者授权了 `forms.write` 也不能写。

### 1.1 登记客户端（运维）

```bash
python -m synwevo_identity.cli client register crm-sync --app platform --name "CRM 同步" \
  --redirect https://crm.example.com/oauth/callback \
  --scopes "openid offline_access tables.read tables.write forms.read"
```

未指定 `--audience` 时，登记了业务应用 scope 的客户端默认受众为 `urn:synwevo:platform:suite`。

### 1.2 Scope

| Scope | 允许 |
|---|---|
| `forms.read` / `tables.read` / `dataprep.read` / `extract.read` / `approvals.read` | 该应用的只读操作（`approvals.read` 包含可选审批人名单） |
| `forms.write` | 创建、修改、发布表单，设置公开链接，发出与关闭填写请求，审阅提交 |
| `tables.write` | 新增、修改、归档记录，导入导出，保存视图；表结构（需本人为所有者/管理员） |
| `dataprep.write` | 导入数据、保存与发布处理方案、启动和取消运行 |
| `extract.write` | 创建和发布提取模板，创建与取消提取任务 |
| `extract.decide` | 以本人名义修正并确认提取结果（人工复核） |
| `approvals.write` | 发起、修订、撤回审批请求，添加意见；审批模板（需本人为所有者/管理员） |
| `approvals.decide` | 以本人名义作出审批决定、转交本人任务、修正停滞环节的审批人 |

`*.decide` 单独存在：同意“写入”不等于同意“替我做决定”。同意页逐项列出客户端请求的 scope。

每个操作需要哪个 scope 由操作自身的声明推导（`capabilities.machine_scope`），`GET /operations` 与契约里都写明。
**处理函数触达的一切同样受凭据的 scope 约束**，无论它以什么方式发起内层调用：

* 跨应用：`dataprep.dataset.from_forms` 需要 `dataprep.write` **和** `forms.read`，`dataprep.dataset.from_extract` 需要
  `extract.read`，`extract.file.from_forms` 需要 `extract.write` 和 `forms.read`（契约的 `security` 与 `/operations` 的
  `also_requires` 列出），缺少时在调用开始前按名拒绝（`details.required_scope`）。打开一个来自 Forms/Extract 的数据集
  会重新读取来源；缺少来源应用的 read scope 时同样拒绝，但这次**不给出 scope，也不说明记录来自哪个应用**
  （403 `insufficient_scope`，没有 `details`）——一条记录的来源不该从错误里读出来。列表里这样的行被隐去并计数（`hidden`）。
* 同一应用内：内层操作仍需要它自己的 scope。唯一的放宽是“写/决定操作的处理函数读取本应用自己的记录”由该 write /
  decide scope 覆盖；这不等于持有 read scope——直接调用只读操作仍需 `<app>.read`。
* 列表不因个别行而整体失败：`dataprep.dataset.list` / `dataprep.run.list` 把来源此刻不可核实的行**隐去并计数**
  （`hidden`），其余照常返回。

### 1.3 不在这个接口里的

* **个人 Agent 的一切**（会话、记忆、个人自动化、报价、预算、付款同意、发起对话）：没有路由，也没有 scope；
  能力层对任何 `via="api"` 调用一律返回 403 `person_required`。
* 跨工作空间写入（`forms.form.duplicate`）：凭据只属于一个工作空间。
* 服务账户的订阅、面向本人或空间管理员的订阅列表：尚未提供（§2.6 末尾）。异步操作的结果通过对应应用的读取操作或
  `GET /runs/{id}` 查询；发生了什么用事件流拉取（§2.5）或签名 Webhook 推送（§2.6）。

## 2. 调用

```bash
curl -sS https://workplace.synwevo.com/api/suite/v1/ops/tables.record.create \
  -H "Authorization: Bearer $SYNWEVO_SUITE_ACCESS_TOKEN" -H "Content-Type: application/json" \
  -H "Idempotency-Key: contact-00042" \
  -d '{"input": {"table_id": "tbl_…", "cells": {"fld_…": "00042"}}}'
```

| 路径 | 说明 |
|---|---|
| `GET /whoami` | 客户端、本人、工作空间、当前角色、仍然有效的 scope、可用应用 |
| `GET /operations[?app=]` | 本人在该工作空间可用的全部操作契约（输入/输出 JSON Schema、所需 scope、幂等规则、是否已授予） |
| `POST /ops/{operation}` | 执行一个操作。请求体 `{input, idempotency_key?, correlation_id?, workspace_id?}`，不接受其他成员 |
| `GET /runs/{run_id}` | 本客户端为本人发起的一次运行的回执；读取时重新鉴权，来源已不可读时 `snapshot_hidden: true`、`output: null` |

响应 `{run_id, operation, status, output, replayed, workspace_id}`。`200` 表示已完成；异步操作尚未结束时为 `202`
（`status` 为 `queued` / `running` / `waiting_input` / `waiting_approval`）——**接纳不是完成**。失败是错误响应，不会出现
“200 且 status=failed”。

`workspace_id` 可省略；若给出必须等于凭据绑定的工作空间，否则 403 `workspace_mismatch`。

### 2.1 文件

上传、下载与导出是各应用**同一个处理函数**在本前缀下的第二次挂载：上传检查（格式、大小、安全扫描与隔离判定）、来源复核、
本人的导出权（编辑者及以上，或带导出权的成员）与页面完全相同；凭据只改变“谁在问”。每条都要带
`?workspace_id=<凭据的工作空间>`（其他值 403 `workspace_mismatch`）。

| 路由 | scope | 说明 |
|---|---|---|
| `POST /extract/files` | `extract.write` | 上传 PDF、图片或 CSV/TSV，供 `extract.task.create` 使用。**必须**带 `Idempotency-Key`：同键同内容返回同一个文件，同键不同内容 409 |
| `GET /extract/files/{file_id}` | `extract.read` | 下载原文件 |
| `GET /extract/tasks/{task_id}/export?revision=` | `extract.read` | 已确认的结果版本（JSON 附件）；未确认或已被替代 409 |
| `POST /dataprep/files` | `dataprep.write` | 上传 CSV / XLSX，供 `dataprep.dataset.import` 使用。**必须**带 `Idempotency-Key` |
| `GET /dataprep/datasets/{dataset_id}/export?format=csv\|xlsx\|json` | `dataprep.read` | 导出数据集；来源来自其他应用时还需要该应用的 read scope |
| `GET /dataprep/runs/{run_id}/issues` | `dataprep.read` | 一次运行的全部异常（JSON 附件） |
| `POST /tables/{table_id}/imports` | `tables.write` | 上传 CSV / XLSX 暂存为导入单，再用 `tables.import.apply` 映射并写入。没有幂等键：重复上传会多一张导入单，SDK 不自动重试。导入单只属于建立它的应用：同一个人授权的另一个应用、本人在页面里，都查不到它（404） |
| `GET /tables/exports/{export_id}` | `tables.read` | 下载用 `tables.export.create` 建立的导出：下载**那一刻**本人可读的记录。导出单只属于建立它的应用（规则同上；页面里建立的导出单，应用也取不到），`tables.export.create` 返回的 `url` 就是这个地址 |
| `GET /forms/files/{file_id}` | `forms.read` | 下载提交的附件（`forms.submission.get` 输出里的 `files[].id`） |

操作输出里的 `url` 字段（附件、导出）是页面路径；凭据请用上表的路径和输出里的 `id`。上传键与操作的幂等键一样按客户端
命名空间保存，页面不能使用 `api:` 前缀。下载是带 `no-store` 的直接响应，没有可转交的签名链接——撤权后下一次下载即被拒绝。

### 2.2 幂等

* 有副作用的操作支持 `Idempotency-Key`（或请求体 `idempotency_key`，两者同时给出必须一致）：1–128 个可见 ASCII 字符。
  同键同参返回第一次的结果（`replayed: true`）；同键不同参 409 `idempotency_conflict`；先前失败的同键请求 409
  `idempotent_failure`，不会重新执行。
* 会向他人发出请求、通知或链接的操作，以及处理函数以该键识别重试的操作（契约中 `x-synwevo-idempotency: required`，如
  `forms.request.create`、`extract.file.from_forms`）**必须**带键，否则 400 `idempotency_key_required`。
* 命名空间是“本人 + 操作 + 工作空间 + 客户端”。轮换客户端密钥或换一个访问令牌不改变命名空间；两个不同客户端使用同一个
  键互不影响；页面上的同名键也互不影响。凭据的键以 `api:<客户端>:` 前缀存储，这个前缀对页面、流程和 Agent 一律拒绝；
  一次运行只会重放给**同一类调用方**——页面产生的运行永远不会作为凭据调用的“重放结果”返回，反之亦然（409
  `idempotency_conflict`）。
* 只读操作忽略键：客户端可以无条件带键并重试，读到的是当下的数据。
* 重放前重新鉴权：原结果的来源此刻不可读时返回 409 `run_result_unavailable`，不会重新执行。
* 超时意味着结果未知：用同一个键重试，或查询 `GET /runs/{id}`。

### 2.3 错误

与 Docs 机器接口相同的信封（`Accept: application/problem+json` 时为 RFC 9457）：

```json
{"error": {"code": "insufficient_scope", "message": "…", "request_id": "…", "details": {"required_scope": "forms.write"}}}
```

| 状态 | `code`（常见） |
|---|---|
| 400 | `invalid`（请求体不合法、含未知成员）、`bad_idempotency_key`、`idempotency_key_required` |
| 401 | `unauthorized`、`invalid_audience`、`client_unavailable` |
| 403 | `insufficient_scope`、`person_required`、`workspace_mismatch`、`read_only_role`、`manage_required`、`app_access_denied`、`wrong_credential`、`forbidden` |
| 404 | `unknown_operation`、`run_not_found`、`app_unavailable`、`not_found` |
| 409 | `idempotency_conflict`、`idempotent_failure`、`run_result_unavailable`，以及各应用的业务冲突（修订号、状态） |
| 422 | `invalid_input`（`details.fields` 逐项列出） |
| 429 | `rate_limited`（`Retry-After`；与 Docs 接口共用按凭据、按工作空间的限额） |

每个响应带 `X-Request-ID`；成功与 429 响应带 `RateLimit` / `RateLimit-Policy`。调用记录（接口模板、状态、错误代码、
客户端、请求编号，不含请求体）进入 API 控制台的调用记录；写操作进入工作空间审计，署名为本人并注明客户端。

### 2.4 撤权

每次调用重新核对，以下任一发生后**下一次调用**即被拒绝：本人在「已授权的应用」撤回、令牌被撤销、刷新令牌被重放（整组
授权撤销）、客户端被停用或缩小登记范围、本人被移出工作空间/降为查看者/账户停用、工作空间冻结、应用在该空间停用。

**延后工作随授权一起结束。** 客户端发起的 DataPrep 运行、Extract 任务与审批等待，在每个复核点（开始前、处理中、发布
结果的同一事务内）重新核对发起它的授权：授权撤回、令牌撤销、刷新令牌重放、客户端停用或不再登记所需 scope 之后，下一步
即被拒绝，运行以 `grant_revoked` / `credential_revoked` / `scope_withdrawn` 失败，此后不再写入任何结果（不产生输出数据集、
不保存解析结果、不把审批决定交给等待方）。运行读取其他应用的来源时，同样只在授权**此刻**仍持有的 scope 范围内。
刷新轮换和访问令牌自然过期不算撤回。本人在页面里发起的工作不受任何客户端授权影响。与 Synwevo Docs 任务的规则一致。

### 2.5 事件流（拉取）

`GET /events?cursor=&types=&limit=&start=` 回答“自上次以来这个工作空间发生了什么”。

```json
{"workspace_id": "ws_…", "has_more": false, "next_cursor": "s1.…",
 "items": [{"id": "evt_…", "type": "tables.record.changed", "version": 1, "app": "tables",
            "object": {"kind": "table_record", "id": "rec_…"}, "refs": {"table_id": "tbl_…"},
            "occurred_at": 1790000000.12, "run_id": "run_…", "correlation_id": "sync-7"}]}
```

* **只说去哪里看，不说那里有什么。** 一条事件给出类型、对象、打开对象所需的标识（`refs`，逐个类型声明：记录的
  `table_id`、提交的 `form_id` / `request_id`、运行的 `output_dataset_id`）和时间。事件的数据（备注、审批人、结果）
  **不提供**；要内容就用对应的读取操作，届时照常鉴权。`run_id` / `correlation_id` 只在**引起这条事件的那次运行是本客户端
  自己发起的**时给出（两者同时给出或同时为 null）：其他调用方的关联标识是它们自定的文本，不会转给你，你用了同样的文本
  也不会把它们的事件标成你的；不由任何运行引起的事件（公开链接的提交、到期、后台清理）两者都是 null，请用 `refs` 对应。
  `id` 稳定，可用来去重。
* **能看到哪些。** 只有凭据所在工作空间的事件；只有凭据持有 `<app>.read` 且本人此刻仍可使用的应用（只有 write / decide
  不算；一个 read scope 都没有时 403 `insufficient_scope`，`details.any_of` 列出可用的）。事件是关于一条记录的事实，
  所以**本人此刻读不到的记录没有事件**：Tables 按数据表与记录的读取角色，DataPrep 按数据集 / 运行的来源此刻是否可读
  （与列表里隐去的行同一规则，来源应用的 read scope 也要持有）。之后变得可读的记录，重新从头读取时会出现；已经读过的
  位置之后不补发——应用在该空间被停用、或本人暂时失去访问期间读取，游标同样会越过这段时间的事件，恢复后需要的话请
  重新列出记录。个人 Agent、计费、付款同意、Docs、Message 的事件永远不在这里；可提供的类型见契约
  `x-synwevo-event-types`。
* **游标。** 保存每页的 `next_cursor` 并原样送回。它不透明、不可伪造，只属于“这个客户端 + 这个人 + 这个工作空间”：换一个
  客户端、换一个人或换一个工作空间出示都是 400 `invalid_cursor`。游标不是授权——每次读取都按**此刻**的 scope 与权限
  过滤：授权缩小后，同一个游标读到的更少；授权扩大后，新应用的事件从游标所在位置开始，不回补。
* **分页有界。** `limit` 1–500（默认 100）。`has_more: true` 表示这次请求在流的末尾之前停下了——即使 `items` 为空，
  或不足 `limit` 条：一次请求读取的日志行数有上限（本工作空间的事件，不论属于哪个应用、你是否可见，都计入），需要
  应用逐条判断可见性的行数另有更小的上限——带着 `next_cursor` 再读即可。到达末尾时 `has_more: false`，之后按自己的
  节奏轮询。`types=a,b` 只要指定类型（未知类型 400 `invalid_events`；属于未持有 read scope 的应用 403）。
* **从哪里开始。** 不带游标默认从保留期内最早的事件开始（`start=oldest`）；`start=now` 不返回事件，只给出“现在”的
  游标，适合只关心今后的集成。
* **保留期 30 天。** 不带游标的读取从 30 天前开始。游标在“它之后的事件不早于 30 天再加 24 小时宽限”时有效：读到末尾
  的游标自签发起算，停在中途的游标以它读到的最后一条事件的时间起算（宽限保证从最早处开始翻页的客户端不会刚拿到游标
  就过期）。超过之后是 410 `cursor_expired`（`details.retention_seconds`、`details.grace_seconds`），**不会**跳过缺口
  继续——请重新列出你关注的记录，再不带游标重新开始。数据库从更早的备份恢复后，与日志不再一致的游标同样是 410——无论
  日志此刻仍落后于游标，还是已经重新增长并越过了它。服务端根密钥更换后，旧游标是 400 `invalid_cursor`，同样重新开始。
* 投递语义是“至少一次、按序”：处理完一页再保存游标；保存前崩溃会重读到同一页，用 `id` 去重。

### 2.6 签名 Webhook（推送）

事件流的推送形式：订阅一个 https 地址，之后**这份凭据此刻能从 `GET /events` 读到的事件**会被签名后 POST 过去。内容与
事件流的条目完全相同（只说去哪里看，不含记录的数据），`webhook-id` 就是事件的 `id`——推和拉可以共用一张去重表。

| 路由（前缀 `/api/suite/v1/webhooks/subscriptions`） | 作用 |
|---|---|
| `POST ""`（201） | 创建；应答里的 `secret`（`whsec_…`）**只出现这一次** |
| `GET ""`、`GET /{id}` | 本授权自己的订阅（`limit`、`workspace_limit` 一并给出） |
| `PATCH /{id}` | 改地址、事件或说明；`If-Match: "<version>"`（或 body 里的 `version`）防止覆盖别人的修改，冲突 409 `version_conflict` |
| `POST /{id}/pause`、`/resume` | 暂停 / 恢复 |
| `POST /{id}/rotate-secret` | 换签名密钥（`overlap_s` 0–86400，默认一天） |
| `POST /{id}/ping` | 立即发送一条签名的 `webhook.ping` |
| `GET /{id}/deliveries`、`GET /{id}/deliveries/{did}` | 投递记录（最新在前，`cursor` 翻页，`status` 过滤） |
| `POST /{id}/deliveries/{did}/redeliver`（202） | 把那次投递的事件再排一次 |
| `DELETE /{id}` | 删除：队列清空，密钥销毁 |

`events` 是事件类型、`"<应用>.*"` 或 `"*"`（凭据此刻可读的全部）的列表；点名一个未持有 read scope 的应用是 403
`insufficient_scope`。创建、换密钥、ping、重新投递**不接受 `Idempotency-Key`**（400 `idempotency_not_supported`）：
应答里有密钥或会触发一次发送，都不能被存下来重放。

**收到的请求**

```
POST <你的地址>
webhook-id: evt_…                    事件 id，与事件流里的 id 相同
webhook-timestamp: 1790000123        本次尝试的时间（不是事件发生的时间；那是 body 里的 occurred_at）
webhook-signature: v1,<base64>       轮换重叠期内有两个，空格分隔，任一匹配即可
X-Synwevo-Subscription: whsub_…      哪个订阅——也就是用哪个密钥验证
X-Synwevo-Delivery: whdel_…          这条投递记录
X-Synwevo-Event: tables.record.changed

{"id":"evt_…","type":"tables.record.changed","version":1,"app":"tables","object":{"kind":"table_record","id":"rec_…"},
 "refs":{"table_id":"tbl_…"},"occurred_at":1790000000.12,"run_id":null,"correlation_id":null,
 "workspace_id":"ws_…","subscription_id":"whsub_…"}
```

* **验证**（Standard Webhooks）：对**原始请求体字节**计算 `HMAC-SHA256(密钥, "<webhook-id>.<webhook-timestamp>.<body>")`，
  与任一 `v1,` 签名做定长比较；`webhook-timestamp` 与当前时间相差超过几分钟的拒绝。不要对重新序列化的 JSON 验签。
  SDK：`verify_webhook(headers, body, secret)` / `verifyWebhook(...)`。
* **2xx 表示收下**，须在 15 秒内应答；响应体不会被读取。**不跟随重定向**（3xx 算失败）。**410 表示“别再发了”**：订阅
  停用（`gone`）。其余应答和连接失败按 5 秒、5 分钟、30 分钟、2、5、10、14、20、24 小时重试（±20%；429 / 503 的
  `Retry-After` 是下限），共 10 次后该条记为 `failed`。请先收下再处理：因为处理不了而应答非 2xx，会让后面的事件一直
  排在它之后。
* **顺序与去重。** 一个订阅的投递按入队顺序一条一条发：最早的一条在重试期间，后面的都不发（ping 不在此列）。语义是
  “至少一次”——用 `webhook-id` 去重。**同一个地址服务多个人的授权时**，每个授权有自己的订阅、自己的密钥、各自判定的
  一份：同一事件会以相同的 `webhook-id` 到达多次（`run_id` / `correlation_id` 只在发起者那一份里），去重键是
  `(subscription_id, webhook-id)`，属于谁以**验签通过的那个密钥**为准；`subscription_id` 在签名覆盖的 body 里。
* **不会淹没接收端。** 一个订阅最多有 100 条等待中的投递，其余留在日志里按序跟上；同一主机每个工作进程每分钟最多
  300 个请求（所有订阅合计）。平台自身的故障（解析器繁忙、主机额度用完、签名密钥暂不可读）不计入尝试次数，也不影响
  订阅的健康度（记录里是 `resolver_busy` / `destination_busy` / `signing_key_unavailable`）。

**谁为订阅负责。** 订阅属于“这个客户端 + 这个人 + 这个工作空间”，别的客户端、别的人、别的空间看到的是 404。投递按
**绑定它的那组授权**判定——创建它、恢复它、或最后一次改它的地址 / 事件 / 密钥的那一组（只改说明不算）。这些调用把订阅
绑定到调用方的授权，并按调用方持有的 scope 重新核对整个事件过滤器。`authorized_by_caller` 说明当前调用的授权是不是
负责的那一组；重新授权（得到新的一组令牌）之后，对仍在运行的订阅调一次 `resume` 即可改绑——不会跳过任何事件。

**授权必须可续期。** 订阅没有自己的期限，所以背后的授权必须是客户端此刻还能刷新的：申请授权时带 `offline_access`，
否则创建是 403 `grant_not_renewable`。入队时和**每次发送之前**（重试可以持续几天）都重新核对；以下任一成立，订阅停用为
`authorization_revoked:<原因>`，队列清空，不再发出任何请求：

| 原因 | 含义 |
|---|---|
| `grant_revoked` | 本人撤回、令牌被撤销、刷新令牌被重放 |
| `grant_expired` | 刷新令牌已过期——**只推不拉的集成也要按时刷新令牌**（刷新令牌默认 14 天有效） |
| `session_ended` | 授权所依据的登录被结束（退出、结束设备、管理员撤销会话） |
| `entitlement_revoked`、`not_a_member`、`account_unavailable`、`workspace_unavailable` | 本人不再能在该空间使用 |
| `credential_revoked` | 客户端被停用 |
| `grant_not_renewable`、`unknown_principal` | 授权不可续期 / 无法确认 |

只是**少了一个 scope、应用在该空间停用、或某条记录本人不再可读**时不停用订阅：对应的事件不发（投递记录为
`dropped` / `no_longer_readable`），与事件流的过滤一致。其他停用原因：`failing`（连续 3 天且整整一轮 10 次尝试没有一次
成功）、`backlog_expired`（落后超过事件流的保留期）、`journal_replay`（数据库从更早的备份恢复，而此后订阅曾被暂停、
改地址、换密钥或停用：请核对地址、重新换密钥后再 `resume`）。任何停用状态都用 `resume` 重新启用。停用超过 30 天的
`authorization_revoked` 订阅会被删除（密钥一并销毁）。

**停了之后补什么。** 暂停和停用都会丢弃等待中的投递，恢复后**从当下开始**，中间发生的不补发。订阅的
`missed_events_cursor` 是“上次停在哪里”的事件流游标（属于同一授权，恢复后仍保留）：`resume` 之后用它读
`GET /events?cursor=…` 就是恰好没推的那一段，按 `id` 去重——包括这次停止之前、自上一次成功投递以来已经用尽重试而记为
`failed` 的事件。它超过事件流保留期后为 null（此时请重新列出记录）。
只推不拉的集成请定期读一下订阅（`status`、`disabled_reason`、`stalled_since`）。`stalled_since` 非空表示平台一侧出错、
有一条事件暂时无法判定：它之后的事件都在等，不会被跳过。

**密钥。** 只在创建和轮换的应答里出现一次（`Cache-Control: no-store`），服务端密封保存。轮换后旧密钥在 `overlap_s`
内继续一起签名（`previous_secret_expires_at`）。**重叠期内再次轮换只替换最新的那一把**，更早的那把及其到期时间不变
——所以轮换的应答丢了可以直接再轮换一次，接收端手里的旧密钥不会被挤掉；`overlap_s: 0` 立即作废所有旧密钥。
同一授权对同一地址只能有一个订阅：创建的应答丢了再创建一次，会得到 409 `subscription_exists`（`details.id` 是已有的
那个），对它 `rotate-secret`（`overlap_s: 0`）即可拿到新密钥。

**地址。** 只接受 https、可公开路由的地址，URL 里不能带账号密码；创建、修改、恢复时检查，**每次发送前再检查一次**，并且
只连接到刚检查过的那个地址（主机名不会被解析第二次）。“无法解析”和“解析到不允许的地址”是同一个拒绝，不加区分。
创建、修改、恢复时如果同时在检查的地址太多、或解析器一时都被占用，应答是 429 `destination_check_busy`（带
`Retry-After`）：这不是对地址的判断，什么都没有改变，稍后原样重试即可。
投递记录的 `last_error` 是固定词表（`http_status`、`redirect_not_followed`、`timeout`、`tls_failed`、`connect_failed`、
`destination_unavailable` 等），从不包含接收端的应答或平台的网络细节。

**限额。** 每组授权 4 个订阅（含已停用的，可自行删除）；每个客户端在一个工作空间 32 个、每个工作空间 64 个启用或暂停的
订阅（运维可调，超出为 409 `subscription_limit_reached` / `workspace_subscription_limit_reached`）。`ping` 每个订阅
10 秒一次、每小时 20 次（429 `ping_rate_limited`，带 `Retry-After`），不重试，不计入健康度，对 ping 应答 410 不会停用
订阅。重新投递：同一事件最多 5 次，事件发生 30 天内，且订阅须处于启用状态；重新投递同样先过全部检查。投递记录保留
30 天。

**记录。** 创建、改地址或事件、换密钥、暂停、恢复、删除都写入工作空间审计（本人、客户端、目标主机；不含密钥，也不含
地址的路径）。

**尚未提供。** ① 本人和空间管理员还看不到“哪些地址在接收我的事件”的列表——现在要停止推送，请在「已授权的应用」撤回
对该客户端的授权（立即生效），或让集成方删除订阅；② 接收地址不需要证明自己愿意接收（没有握手），靠上面的限额约束；
③ 不能按客户端登记允许的接收主机；④ 服务账户不能订阅。见 [API 覆盖情况](../platform/api-coverage.md)。

## 3. SDK

Python（`synwevo_docs_sdk`）与 TypeScript（`@synwevo/docs-sdk`）各有独立的 `SuiteClient` 与 `UserToken`：

```python
from synwevo_docs_sdk import SuiteClient, UserToken

token = UserToken(refresh_token=stored["refresh_token"], client_id="crm-sync", client_secret=secret,
                  token_url="https://accounts.synwevo.com/oidc/token",
                  resource="urn:synwevo:platform:suite", on_rotate=store.save)
with SuiteClient(credentials=token) as suite:
    made = suite.invoke("tables.record.create", {"table_id": tid, "cells": {fid: "00042"}},
                        idempotency_key="contact-00042")
    print(made["output"], made.replayed)
```

```ts
import { SuiteClient, UserToken, SUITE_RESOURCE } from '@synwevo/docs-sdk';

const token = new UserToken({ refreshToken: stored.refreshToken, clientId: 'crm-sync', clientSecret,
  tokenUrl: 'https://accounts.synwevo.com/oidc/token', resource: SUITE_RESOURCE, onRotate: (t) => store.save(t) });
const suite = new SuiteClient(undefined, token);
const made = await suite.invoke('tables.record.create', { table_id: tid, cells: { [fid]: '00042' } },
  { idempotencyKey: 'contact-00042' });
```

* 每次 `invoke` 都带幂等键（你的，或为这一次调用生成的），网络失败与 5xx 用同一个键重试。
* **刷新令牌只能用一次，而且只能有一个持有者。** 每次刷新返回后继者，SDK 在使用新访问令牌之前把它交给
  `on_rotate` / `onRotate`——务必保存：再次出示已用过的刷新令牌会撤销本人的整组授权。因此**不要让两个进程（或两台机器、
  一个进程里的两个 `UserToken`）从同一个刷新令牌启动**：第一个刷新后，第二个出示的就是已用过的令牌，平台按泄露处理，
  两边同时失效，本人必须重新授权。多进程部署时由一个进程（或一个带锁的共享存储）负责刷新，其余只读取当前访问令牌；
  `from_env` / `fromEnv` 读到的是启动时的那一个刷新令牌，只适合单进程。
* 文件：`extract_upload` / `extractUpload`、`extract_download`、`extract_export`、`dataprep_upload`、`dataprep_export`、
  `dataprep_run_issues`、`tables_import_upload`、`tables_export_download`、`forms_file`（TypeScript 为对应的驼峰命名）。上传
  接受磁盘路径或字节；带键的上传用同一个键重试，`tables_import_upload` 不重试。
* 事件流：`events_page` / `eventsPage`（一页）、`iter_event_pages` / `eventPages`（逐页，`follow` 时持续轮询）、
  `iter_events` / `events`（逐条）。游标过期是 `GoneError`（`cursor_expired`）。处理完一页后保存该页的 `next_cursor`。
* Webhook：`create_webhook` / `createWebhook`、`list_webhooks`、`get_webhook`、`update_webhook`（`version`）、
  `pause_webhook`、`resume_webhook`、`rotate_webhook_secret`、`ping_webhook`、`webhook_deliveries`、`webhook_delivery`、
  `redeliver_webhook`、`delete_webhook`（TypeScript 为对应的驼峰命名）。会返回密钥或触发发送的调用不自动重试。接收端用
  `verify_webhook` / `verifyWebhook` 验签（与 Docs 的 Webhook 同一个函数）。
* `from_env` / `fromEnv` 只读 `SYNWEVO_SUITE_*` 变量，不借用 Docs 或目录接口的凭据。
* 尚未发布到公共包仓库；从本仓库安装（`apps/docs/sdk/python`、`apps/docs/sdk/typescript`）。

## 4. 验证

| 内容 | 位置 |
|---|---|
| 身份、scope、单一工作空间、实时撤权、个人操作无入口、幂等、回执、调用记录与审计 | `apps/suite/tests/test_machine_api.py` |
| 应用自身的操作全部按构建结果入档、路由与契约一致 | `apps/suite/tests/test_machine_contract.py` |
| 发布的契约 = 组装后部署的注册表 = 导出器输出；组装层操作的 scope 与幂等声明与实际一致 | `tests/integration/test_suite_machine_contract.py` |
| 文件路由：scope / 工作空间 / 会话不算凭据、上传键命名空间、与页面相同的上传检查、导出权与跨应用上限、三条无浏览器的完整流程 | `apps/suite/tests/test_machine_files.py` |
| Python SDK 对真实应用（含文件上传 → 处理 → 导出） | `apps/suite/tests/test_machine_sdk.py` |
| 授权结束后延后工作停止（五种结束方式 × 排队中/处理中途），轮换与过期不误停，页面发起的不受影响 | `apps/suite/tests/test_machine_deferred.py` |
| 真实登录 → 同意 → 令牌 → 三个机器接口互不通用 → SDK 刷新轮换 → 重放撤销 → 撤回同意 | `tests/integration/test_suite_machine_flow.py` |
| 事件流：条目只含路由信息、仅本工作空间、按 read scope、Tables / DataPrep 逐记录可见性、未声明与个人事件不提供、游标密封且只属于持有者、授权缩小即少、恰好一次按序、扫描上限、保留期与 410、契约与实际应答一致 | `apps/suite/tests/test_machine_events.py` |
| 流程的事件触发器只对所有者此刻可读的记录触发（读取角色、来源丢失、重新可读后不补发） | `apps/suite/tests/test_flow_trigger_visibility.py` |
| 签名 Webhook：投递 = 事件流条目且同 id、密钥只出现一次并密封、订阅只属于其授权、入队与每次发送前复核（八种授权结束方式 × 已入队/未入队）、授权须可续期、绑定规则、按序与队列上限、落后即停、停止处的补读游标、无法判定时原地等待、本端故障不计尝试、主机额度、未回报的尝试仍计数、地址策略与连接固定、轮换重叠、ping 限额、恢复后不复活、审计、契约与实际应答一致 | `apps/suite/tests/test_machine_webhooks.py` |
| SDK 重试、键、令牌轮换、事件流翻页与跟随（传输故障夹具） | `apps/docs/sdk/python/tests/test_suite.py`、`apps/docs/sdk/typescript/test/suite.test.mjs` |
