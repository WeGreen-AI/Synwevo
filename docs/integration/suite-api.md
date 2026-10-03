# Synwevo 业务应用操作接口（`/api/suite/v1`）

Synwevo Forms、Tables、DataPrep、Extract、Approvals 的机器接口。它不是另一套实现：每个操作与应用页面调用的是同一个
入口（`Capabilities.invoke`），校验、实时角色、运行记录、幂等与事件完全一致；这一层只增加凭据才需要的东西。

* 契约：[`suite-openapi.json`](suite-openapi.json)（OpenAPI 3.1，由运行时注册表生成；`python -m synwevo_suite.cli openapi --out …`
  重新生成，测试会比对）。
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
**跨应用读取同样受 scope 约束**：`dataprep.dataset.from_forms` 需要 `dataprep.write` 和 `forms.read`；打开一个来自
Forms/Extract 的数据集会重新读取来源，缺少来源应用的 read scope 时拒绝。

### 1.3 不在这个接口里的

* **个人 Agent 的一切**（会话、记忆、个人自动化、报价、预算、付款同意、发起对话）：没有路由，也没有 scope；
  能力层对任何 `via="api"` 调用一律返回 403 `person_required`。
* 跨工作空间写入（`forms.form.duplicate`）：凭据只属于一个工作空间。
* 文件上传与下载（Forms 附件、Tables 导入导出文件、DataPrep 上传、Extract 文件）：这些路由目前只接受会话。相关操作可以
  调用，但文件环节需在页面完成——尚未实现的下一步。
* 事件订阅 / Webhook：尚未实现。异步操作的结果通过对应应用的读取操作或 `GET /runs/{id}` 查询。

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

### 2.1 幂等

* 有副作用的操作支持 `Idempotency-Key`（或请求体 `idempotency_key`，两者同时给出必须一致）：1–128 个可见 ASCII 字符。
  同键同参返回第一次的结果（`replayed: true`）；同键不同参 409 `idempotency_conflict`；先前失败的同键请求 409
  `idempotent_failure`，不会重新执行。
* 会向他人发出请求、通知或链接的操作（契约中 `x-synwevo-idempotency: required`，如 `forms.request.create`）**必须**带键，
  否则 400 `idempotency_key_required`。
* 命名空间是“本人 + 操作 + 工作空间 + 客户端”。轮换客户端密钥或换一个访问令牌不改变命名空间；两个不同客户端使用同一个
  键互不影响；页面上的同名键也互不影响。
* 只读操作忽略键：客户端可以无条件带键并重试，读到的是当下的数据。
* 重放前重新鉴权：原结果的来源此刻不可读时返回 409 `run_result_unavailable`，不会重新执行。
* 超时意味着结果未知：用同一个键重试，或查询 `GET /runs/{id}`。

### 2.2 错误

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

### 2.3 撤权

每次调用重新核对，以下任一发生后**下一次调用**即被拒绝：本人在「已授权的应用」撤回、令牌被撤销、刷新令牌被重放（整组
授权撤销）、客户端被停用或缩小登记范围、本人被移出工作空间/降为查看者/账户停用、工作空间冻结、应用在该空间停用。

已经排队的延后工作（DataPrep 运行、Extract 任务、审批等待）属于本人：按本人的实时权限继续或停止，不取决于客户端的授权
是否仍在。

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
* **刷新令牌只能用一次。** 每次刷新返回后继者，SDK 在使用新访问令牌之前把它交给 `on_rotate` / `onRotate`——务必保存：
  再次出示已用过的刷新令牌会撤销本人的整组授权。
* `from_env` / `fromEnv` 只读 `SYNWEVO_SUITE_*` 变量，不借用 Docs 或目录接口的凭据。
* 尚未发布到公共包仓库；从本仓库安装（`apps/docs/sdk/python`、`apps/docs/sdk/typescript`）。

## 4. 验证

| 内容 | 位置 |
|---|---|
| 身份、scope、单一工作空间、实时撤权、个人操作无入口、幂等、回执、调用记录与审计 | `apps/suite/tests/test_machine_api.py` |
| 契约与运行时一致、路由全部入档 | `apps/suite/tests/test_machine_contract.py` |
| Python SDK 对真实应用 | `apps/suite/tests/test_machine_sdk.py` |
| 真实登录 → 同意 → 令牌 → 三个机器接口互不通用 → SDK 刷新轮换 → 重放撤销 → 撤回同意 | `tests/integration/test_suite_machine_flow.py` |
| SDK 重试、键、令牌轮换（传输故障夹具） | `apps/docs/sdk/python/tests/test_suite.py`、`apps/docs/sdk/typescript/test/suite.test.mjs` |
