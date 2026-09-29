# 机器接口（`/api/v1`）

与界面**共用同一套引擎、服务与任务系统**：同样的输入、模板版本和规则，通过界面或接口得到同样的业务结果。
接口不依赖浏览器、页面、DOM 或鼠标选区。受控 CLI 与平台管理员专用 API 台可管理凭据与回调；
普通用户的文档业务接口仍按正常空间和对象权限使用。控制台说明见下节。

## 管理员 API 台（本机开发轮，2026-09-25）

账户源站 `/admin/api-platform` 及 `/api/admin/api-platform/*` 要求有效平台管理员的
`api_platform.manage`。组织所有者通过 `/account/api-approvals` 批准**自己有权管理的空间**；
该页不展示机器秘密，也不是组织成员的 API 控制台。批准、scope、空间状态、批准人的当前所有者资格
在凭据签发和每次机器调用时再次检查。撤销批准不能通过重新启用客户端绕过。

控制台包括真实调用元数据/状态汇总、一次性秘密展示、服务身份和旧式密钥生命周期、实际 OpenAPI 文档，
以及受限凭据调试与 Webhook 管理。「Webhook」页签顶部的投递运行概况（API-6，`GET /api/admin/api-platform/webhooks`）跨工作空间列出订阅健康：接收方只显示来源（scheme://host:port，路径与查询可能含对方令牌，不显示）、状态、连续失败、排队数、近 7 天成功/失败、最近错误；总览显示事件积压与持续失败的订阅数。平台可对失败或异常的接收方做保护性暂停（`POST …/webhooks/{sid}/pause`，近期重新验证 + 理由 + 审计，排队投递随之丢弃）；恢复、重投与修改仍由工作空间用自己的凭据完成。不显示事件内容或签名密钥。「生成任务」页签（`GET …/jobs`、`POST …/jobs/{jid}/cancel`）跨工作空间列出任务元数据（状态、阶段、计数、来源、提交请求编号，不含项目/模板名称与样本内容），可对失控任务做保护性停止（规则与工作空间自己取消相同，重新验证 + 理由 + 审计）。「审计」页签（`GET …/audit`）只读列出 API 平台相关动作（`api_platform.*`、`oidc.*`、`api.credential.*`、`cli.client.*`），含被拒绝与出错的尝试，可按动作、结果、工作空间、请求编号筛选；控制台审计行记录目标工作空间与所建对象的 id。请求记录只保存路由模板、关联 ID、状态与耗时，不存令牌、请求正文或文档。
机器契约里的路径、字段、错误码和枚举不随界面语言变化。界面说明使用独立三语言资源。

Docs 的实现通过组装层注入 `DocsControlPlane`，账户服务没有导入 Docs 的实现。调试必须提供显式受限
Bearer 凭据，不使用管理员 Cookie 访问文档；只允许已列明的 API 路径，不代理任意 URL 或本机路径。
HTTP 页面访问和每次管理调用都有服务端权限检查；仅隐藏导航不是权限控制。

新凭据的 scope 不超过当前批准。正常轮换不更改批准和资源归属；安全撤销/停用阻止后续调用，
运行任务仍按原引擎的安全检查点验证执行主体。人类登出不撤销组织服务身份。
验证入口：`test_api_platform.py`、`test_api_platform_races.py`、`test_api_platform_pipeline.py`、
`test_api_platform_contract_i18n.py` 以及同目录浏览器验收脚本。当前结果见 [handoff](../quality/handoff.md)。

* 基础地址：`http://<host>:<port>/api/v1`（默认 `http://127.0.0.1:8765/api/v1`，端口见 [ports.md](ports.md)）
* 机器可读规范：[openapi.yaml](openapi.yaml)（由 `python -m synwevo_docs.cli openapi` 从实际路由导出，`apps/docs/tests/test_api_docs.py` 校验其与代码一致）
* 调用示例与脚本：[automation.md](automation.md)

2026-09-24：API 与浏览器均继续复用业务引擎。本轮增加原生文件预览、严格仅失败项重试和当前权限复核；
完整综合验收仍在进行，实际证据与未完成项见 [handoff](../quality/handoff.md)。以下表格按能力组织，
路由/operationId 的完整机器清单以 OpenAPI 和 [Python SDK 覆盖清单](../../apps/docs/sdk/python/operation-coverage.json) 为准。

## 1. 鉴权

```
Authorization: Bearer dw_<key_id>_<secret>
```

```bash
python -m synwevo_docs.cli apikey create --name ci --tenant default --scopes read,write
python -m synwevo_docs.cli apikey list
python -m synwevo_docs.cli apikey rotate <key_id>     # 生成新密钥并撤销旧的
python -m synwevo_docs.cli apikey revoke <key_id>
```

* 密钥只在创建时显示一次，服务器只保存 SHA-256 哈希；**不要**写进前端包、仓库或日志。
* `--scopes`：`read`（业务读取）、`write`（创建 / 修改 / 提交任务）、`admin`（受控治理）。各路由还检查当前对象/空间权限；文件下载额外检查导出资格。权限不足返回 `403 forbidden` 或对应授权错误。
* `--tenant`：凭据绑定的稳定 workspace ID，不能由请求体覆盖。`default` 是兼容空间，平台模式下保留当前认领状态；浏览器使用当前已验证会话选择的 workspace。跨空间对象返回 `404`，不泄露是否存在。
* `--expires-days`：可选有效期；过期返回 `401 key_expired`，撤销返回 `401 key_revoked`。
* 限流（API-1 起，所有服务进程共用同一份计数）：每个凭据默认 300 次/分钟（`SYNWEVO_API_RATE_PER_MIN`），
  同一工作空间全部机器凭据合计默认 1 200 次/分钟（`SYNWEVO_API_WORKSPACE_RATE_PER_MIN`）；平台管理员可在
  API 管理台「流量与配额」按平台、工作空间或单个凭据覆盖（见 §5.1）。轮换后的新密钥与旧密钥共用同一额度
  （按密钥谱系计），换密钥或加一个服务账户都不能绕开工作空间合计。超出返回 `429 rate_limited`，带 `Retry-After`，
  `details` 说明是哪一层（`limit_scope`: `credential`/`workspace`）、上限（`limit`）与决定它的设置层（`policy_source`）。
  官方 Python 与 Node SDK/CLI 对 `429 rate_limited`（网关在路由执行前的拒绝，未产生副作用）按 `Retry-After` 有界重试——包括没有幂等键的写操作；
  其他来源的 429 或没有该错误码的 429 仍只对幂等/带键的调用重试。自写客户端请照此处理，并加抖动退避。

### 1.1 平台机器身份（推荐）

启用平台模式后，`/api/v1` 还接受**平台签发的访问令牌**。两种机器调用者，区别很重要：

| | 服务账户 | 用户委托 |
|---|---|---|
| 背后有人吗 | 没有，它以自己的身份行动 | 有，它代表授权它的那个人 |
| 租户从哪来 | 登记时绑定的工作空间 | **每次调用**重新按成员关系解析 |
| 权限 | 登记时的 `api_scopes` ∩ 本次申请 | 客户端许可 ∩ **当前**角色 ∩ 本次授权 |
| 生命周期 | 独立；有人退出浏览器不影响它 | 随账户状态、成员关系、应用资格一起失效 |
| 流程 | `client_credentials` | 授权码流程签发的访问令牌 |

使用受控 CLI 登记一个服务账户（管理员 API 台是另一个经过批准的入口）：

```bash
python -m synwevo_identity.cli client register \
    --client-id nightly-batch --app docweave --name 夜间批处理 \
    --service --workspace w_xxx --api-scopes "docweave.read docweave.write"
```

必须是机密客户端（密钥就是它的全部凭据），必须绑定工作空间（背后没有人，租户推不出来）。
`client_secret` 只显示一次。

取令牌：

```bash
curl -s -X POST "$ISSUER/oidc/token" \
  -d grant_type=client_credentials \
  -d client_id=nightly-batch \
  -d client_secret="$CLIENT_SECRET" \
  -d scope="docweave.read docweave.write"
# → {"access_token":"…","token_type":"Bearer","expires_in":900,"scope":"…"}
```

然后 `Authorization: Bearer <access_token>`，用法与 `dw_` 密钥完全一致。

接口权限词表：`docweave.read`（等价旧的 `read`）、`docweave.write`（等价旧的 `write`）、`docweave.manage`（治理，独立校验）。
两种词表在同一个检查里都接受，因此没有任何调用点需要知道自己拿到的是哪一种凭据。

**三条硬规则：**

1. **ID Token 不是 API 凭证。** 只带 `openid profile email` 的访问令牌调用 `/api/v1` 会被
   `403 wrong_credential` 拒绝，并说明需要申请带 `docweave.*` 权限的令牌。它证明的是谁登录了，
   不是可以运行什么。
2. **每次调用都重新核验**账户状态、成员关系、应用资格——不看令牌当初记了什么。令牌里记的是
   当初授予了什么，不是现在还成不成立；固化进令牌就是"已撤权的人继续工作到过期"的成因。
3. **当前角色会收窄委托**：签发时是 editor、现在被改成 viewer，那么读仍然可以，写会被拒，
   令牌本身不需要撤销。

**用户委托需要本人同意（API-4）。** 授权请求只含 `openid profile email` 时就是登录，不需要同意；请求 `docweave.*` 或
`offline_access` 时，平台先看此人是否已就「这个客户端 + 这个工作空间 + 这些权限」同意过：没有则把请求暂存在账户服务
（10 分钟、只属于发起它的登录会话），把浏览器带到 `/oauth/consent?request=…`，页面显示客户端名称、每项权限的说明、
工作空间与回调主机；批准才签发授权码（此时再核对一次应用资格），拒绝则回到客户端 `error=access_denied`。管理员登记
客户端不能代替本人同意。已同意的范围只会因再次同意而扩大；本人在「账户与安全 → 已授权的应用」撤回后，这个客户端在
该工作空间的全部令牌立即失效（每次调用都查库），下次需要重新同意；撤回写入安全日志，恢复备份后同样生效。升级前已存在的
委托记为「旧授权」，同样可见、可撤回。

令牌端点与撤销端点接受 `client_secret_basic`（RFC 6749 §2.3.1）与 `client_secret_post`，一次请求只能用一种。
机密客户端可用 RFC 7662 自省：`POST /oidc/introspect`（`token`，可选 `token_type_hint`），只回答签发给调用方自己的令牌，
其他令牌与未知令牌一样回 `{"active": false}`；`active` 按此刻判断（撤销、过期、客户端停用、本人账户状态）。

**旧的 `dw_` 密钥继续有效**，不设废止期限。它们早于平台存在，一次性全部作废会打断正在运行的
自动化。两种凭据走共享包的 `packages/core/synwevo_core/machine.py`，产出包含主体种类的 principal。

`tests/integration/automation_example.py` 两种都支持：给 `--key` 用旧密钥，给
`--issuer --client-id --client-secret` 用服务账户。

### 1.2 工作空间目录接口（API-5）

账户服务上的 `/api/workspace/v1` 是另一个机器接口：一个工作空间的成员、角色与邀请，给该空间所有者批准的**服务账户**。
它有自己的资源 `urn:synwevo:platform:workspace`（`SYNWEVO_WORKSPACE_RESOURCE` 可改）与权限 `workspace.read`；
申请令牌时带 `resource=urn:synwevo:platform:workspace`。Docs 的密钥与令牌不能调用它（受众不符 401 `invalid_audience`；
`dw_` 密钥 403 `wrong_credential`），它的令牌也不能调用 `/api/v1`。凭据经管理台「授权审批」选择「工作空间目录接口」申请，
所有者批准后签发（只签发服务客户端）；每次调用复核审批、所有者资格与工作空间状态，冻结或撤回即停止。

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/whoami` | workspace.read | 凭据所属工作空间与权限 |
| GET | `/members?role=&q=&offset=&limit=` | workspace.read | 成员：`user_id`、`email`、`display_name`、`role`、`account_status`、`joined_at`；`owner_count` |
| GET | `/invitations?state=&offset=&limit=` | workspace.read | 邀请：`id`、`email`、`role`、`state`（pending/accepted/declined/revoked/expired）、时间 |

只返回目录元数据，不含任何成员创作的内容。同一套限流、调用记录与错误信封（含 RFC 9457）适用。邀请、改角色与移除成员
暂时只在账户页面完成（机器写入需要明确的应用操作者，邀请邮件要写明是谁邀请的，属于下一步 API-5b）。

## 2. 错误与追踪

所有 `/api/v1` 错误的响应体结构一致，HTTP 状态码表达类别：

```json
{ "error": { "code": "invalid_definition", "message": "第 1 个字段「客户」：模板中找不到文字「示例」",
             "field": "find", "request_id": "9f2c…" } }
```

| 状态 | 场景 |
|---|---|
| 400 | 请求内容不合法（`invalid`、`invalid_definition`、`bad_error_policy`、`bad_layout`、`format_unavailable`…）。枚举字段只接受文档列出的值，拼错或大小写不符会被拒绝，**不会**静默退回默认值 |
| 401 / 403 | 密钥缺失/无效/过期/被撤销；权限不足 |
| 404 | 资源不存在或不属于该租户 |
| 409 | 幂等键冲突（`idempotency_conflict`）、映射版本过期（`stale`）、预检发现错误（`has_errors`）、任务状态不允许（`job_active`、`nothing_to_retry`） |
| 410 | 产物已删除 / 链接过期（`artifact_deleted`、`link_expired`） |
| 413 | 超过文件大小上限 |
| 429 | 限流 |
| 500 | 未预期错误（响应含 `request_id`，服务端日志打印同一个 id） |

每个响应都带 `X-Request-ID`；客户端也可自带该头以串联日志。提交任务的那次请求的编号记在任务的 `request_id` 与 `job.created` 事件的 `data.request_id` 里（网页提交同样记录），因此从调用记录、任务到 Webhook 可以沿同一个编号追查。业务校验失败一律用上述状态码，不会伪装成 200。
每个响应还带同一组安全头（`synwevo_core.http.apply_security_headers`，OWASP ASVS 4.0.3 V14.4）：`X-Content-Type-Options: nosniff`、
`Content-Security-Policy`（默认 `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:;
font-src 'self' data:; connect-src 'self'; frame-src 'self' blob:; worker-src 'self' blob:; frame-ancestors 'self'; base-uri 'self';
form-action 'self'; object-src 'none'`）、`X-Frame-Options: SAMEORIGIN`、`Referrer-Policy: no-referrer`、
`Permissions-Policy: camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()`（提供 Synwevo Message 的源——`messages` 角色与单进程 `all`——为 `microphone=(self)`，语音消息在本页录制；其它源关闭）、
`Cross-Origin-Opener-Policy: same-origin`、`Cross-Origin-Resource-Policy: same-site`，以及经 TLS 到达时的
`Strict-Transport-Security: max-age=31536000; includeSubDomains`。机器客户端可以忽略它们；浏览器客户端不能依赖内联脚本或第三方资源。
带 `CF-Ray`（经 Cloudflare）的请求在 `script-src`/`connect-src` 里多出 Cloudflare Web Analytics 的两个源，其余相同。
`SYNWEVO_CSP` 可替换策略（`off` 关闭），见 operations.md §4。契约测试：`tests/integration/test_security_headers.py`。
`Accept: application/problem+json` 可选择 RFC 9457 结构，默认保留原错误 envelope。
每个已认证的 `/api/v1` 响应带 `RateLimit-Policy` 与 `RateLimit`（draft-ietf-httpapi-ratelimit-headers），每层一项：
`"key"`（本凭据，仍排第一，旧解析器照常读取）与 `"workspace"`（工作空间合计），例如
`RateLimit: "key";r=298;t=1, "workspace";r=0;t=20`。`t` 是再多一次请求可用前的秒数。自己解析时请看全部项：
任一项 `r=0` 都表示下一次会被拒绝；两个官方 SDK 取 `r=0` 各项中最长的 `t`。计数存在数据库里（GCRA，整数纳秒），
多个进程合计执行，不是「进程数 × 上限」；数据库暂时不可写时退回本进程计数（仍限流，不放行）。

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
| GET | `/projects/{pid}` | read | 详情：模板绑定、规则、来源文件、映射数、`settings_rev`、`archived_at` |
| PATCH | `/projects/{pid}` | write | `{base_settings_rev, name?, archived?, output?}`：改名、归档/恢复（只从默认列表隐藏，照常可用）、输出默认值；`base_settings_rev` 必填，与当前不符 409 `stale_project`（`details.settings_rev`）；修改检查与写入同一事务，同一版本上的并发修改只有一个成功；其他属性 400 |
| DELETE | `/projects/{pid}` | admin | 删除项目（软删除，产物保留） |
| POST | `/projects/{pid}/files` | write | 上传来源文件：`multipart/form-data` 的 `file` 字段；或 JSON `{"path": "..."}`（仅限 `SYNWEVO_DOCS_IMPORT_ROOTS` 下） |
| GET | `/projects/{pid}/files` | read | 来源文件与解析状态 |
| GET | `/files/{fid}` | read | 单个文件：`status`（`scanning`/`scan_failed`/`parsing`/`ready`/`needs_ocr`/`error`/`rejected`）、`scan`（安全检查结论，见下）、`structure`（工作表、页数、表头等）、`sha256` |
| DELETE | `/files/{fid}` | write | 从项目移除 |

上传后接纳与解析都是异步的：轮询 `/files/{fid}` 直到 `status` 不再是 `scanning`、`scan_failed` 或 `parsing`
（两个 SDK 的 `wait_for_file` / `waitForFile` 就是这样等的）。部署配置了恶意软件扫描时，文件先进入 `scanning`：
引擎判定干净才进入 `parsing`；命中签名则变为 `rejected`（字节已隔离删除，`/files/{fid}/content` 返回 410
`rejected_malware`，同样的字节再上传直接 400 `malware_detected`）；引擎暂不可用或文件超过扫描上限则停在
`scan_failed`（服务端按 30 s→10 min 的间隔自动重试，下载返回 409 `not_yet_accepted`）。**不要因为 `scan_failed`
重新上传**：那只会把同样的字节再排一次队。`scan` 对象给出结论：`status`（`pending`/`clean`/`infected`/`encrypted`/`error`/
`too_large`/`not_configured`/`unscanned`）、`signature`、`engine`（引擎与病毒库版本）、`reason`、`sha256`（结论绑定的内容摘要）、
`checked_at`、`attempts`、`retry_at`。未配置扫描器的部署把每个文件标为 `not_configured`，扫描功能上线前的历史文件为
`unscanned`——两者都不是"干净"。模板上传是同步扫描：命中返回 400 `malware_detected`，引擎不可用返回 503
`scan_unavailable`（`detail.retry_after` 秒后重试），超过扫描上限返回 413 `scan_limit`。`/capabilities` 的
`environment.malware_scan` 说明本部署是否扫描；`/readyz` 的 `checks.malware_scanner` 报告引擎是否可达（`ok`/`off`/
`down: …`），引擎不可达不会让整个服务变为未就绪——已接纳的文件照常工作，新文件留在隔离态。
扫描上线前存入的文件由运维补扫（`docs/security/file-acceptance.md` §8.1）：`scan` 多出 `rescan: true`；判定感染的
历史来源同样变为 `rejected`（`scan.status=infected`），模板的 `scan.status` 为 `infected` 时，凡要用到其内容的接口
（详情、下载、字段与版本操作、预览、生成）回 410 `rejected_malware`，列表仍列出它、仍可删除；项目详情里的 `template`
此时是 `{"id", "name", "format", "rejected": true, "scan": {…}}`，需要换模板。

**设有密码的文件不被接纳（需求 §8.7）。** 开启扫描的部署不接纳加密的压缩包（ZIP/7z/RAR）、设有打开密码的 Office 文档
（含 OpenDocument）和需要密码才能打开的 PDF——安全检查看不到里面的内容，这既不是"干净"也不是恶意软件。只设了权限
（所有者）密码、不输入密码就能打开的 PDF 照常接纳。模板（含可续传上传的使用）直接返回
400 `encrypted_file`（`detail.kind` 为 `archive`/`office`/`pdf`，`detail.filename`），不建模板；去掉密码
（或另存一份不加密的副本）后重新上传即可。**来源文件**与恶意软件扫描走同一条异步路径：上传返回 201、`scanning`，引擎先看（签名优先，
按恶意软件拒绝），随后变为 `error`、`scan.status=encrypted`，`/files/{fid}/content` 回 410 `encrypted_file`，不解析、不预览、不能改解析选项；
结构读不完（截断、超出识别器的上限）同样不接纳——读不完就不能说它干净——但用自己的一句话和代码：`scan.checked=false`，下载 410、模板 400 `unchecked_file`（`detail.checked=false`），因为读者多半没有密码可去。可续传上传在 `/complete` 时字节已经入库，
被拒后没有任何接口能读到它们。补扫发现的扫描上线前存入的加密文件只记下 `scan.status=encrypted`，
状态与可读性都不变——撤回已经在用的文件由所有者决定（`docs/security/file-acceptance.md` §8.1）。

来源选择接口与界面调用同一服务，所有位置从 0 开始。以下列表接口采用 `offset`/`limit`，
默认 50（视图和词列表为 200），最大 200；返回 `total`、`next_offset` 和相对 `Link`。
视图的分页只作用于表格行，其他格式返回现有完整文档视图；分页不代表底层解析按需执行。

| operation ID | 方法 / 路径 | 权限 | 参数与结果 |
|---|---|---|---|
| get_source_structure | GET `/files/{fid}/structure` | read | 解析状态、版本、格式结构 |
| get_source_view | GET `/files/{fid}/view` | read | sheet、offset、limit、block；与界面同一视图结构 |
| list_source_blocks | GET `/files/{fid}/blocks` | read | 当前块拆分规则的样本位置，分页 |
| search_source | GET `/files/{fid}/search` | read | q、block、offset、limit；文字与真实选区，窗口结束时明确 truncated |
| list_source_page_words | GET `/files/{fid}/words/{page}` | read | 已有文字层或已保存 OCR 的词与归一化坐标；不触发 OCR |
| run_source_ocr | POST `/files/{fid}/ocr` | write | `{"pages": [0, 2]}`（可省略：PDF 的全部扫描/混合页；图片即整张），每次最多 50 页，与工作台同一识别引擎、同步完成；结果并入已存 OCR，`needs_ocr` 变为 `ready`、版本前进；返回 `recognized`、`pages`、`words`、`low_conf`、`status`、`version`。未接纳（扫描中、被拒、失败）的来源 409 `source_not_ready`；识别槽位持续占满时 429 `ocr_busy`（带 `Retry-After`）。重复调用对同样页面重新识别、存入同样的词；客户端超时后可用 `words/{page}` 的 `source: "ocr"` 确认哪些页已完成，SDK 不自动重试 |
| update_source_options | PUT `/files/{fid}/options` | write | `{"csv":{"delimiter":";","encoding":"utf-8"}}`；替换 CSV 选项组并重新解析 |
| update_source_correction | PUT `/files/{fid}/corrections/{mid}` | write | 当前映射的 value、严格布尔 confirmed；null 清除修正 |
| select_representative_source | POST `/files/{fid}/representative` | write | 无请求体，选择当前代表来源 |
| reorder_project_sources | PUT `/projects/{pid}/files/order` | write | ids 必须是全部当前来源的完整排列，先验证再写入 |
| download_source_content | GET `/files/{fid}/content` | read + 当前导出权 | 原始文件附件及 X-Content-SHA256 |
| locate_source_mappings | GET `/projects/{pid}/locate` | read | file、block；当前映射在该来源上的位置与值 |
| list_source_records | GET `/projects/{pid}/records` | read | 按当前样本规则分页读取样本摘要 |

未知参数、重复查询键、非法枚举和超范围参数拒绝；JSON 写入上限 256 KiB。
CSV 选项只适用于 CSV/TSV，未提供的组内控件恢复默认值。解析未就绪返回 409 `source_not_ready`。
移除来源或删除项目后，来源读取、修改及迟到的解析结果发布均被拒绝；软删除项目仍保留已生成的独立产物。

续传上传使用 `/uploads` → PUT 字节范围 → `/complete`，完成后以 `upload_id` 绑定来源或模板。
同一主体在幂等保留期内重放创建上传请求，可取回已消费会话的原响应（包括原始 open 状态）；
随后 GET 会话取得当前 consumed 状态，并用原消费幂等键重放来源/模板创建，不再次消费字节。
来源、所属项目或模板删除后，上传元数据、complete 及创建缓存均不能绕过对象授权；不同请求仍冲突。
已取消或过期的未消费会话仍返回 410，新操作使用已消费会话仍返回 409 `upload_consumed`。
完成会先持久化已核验字节，数据库确认后才清理暂存文件；数据库写入失败仍可按同一上传重试。
暂存内容丢失或变短时返回 409 `upload_data_missing` 并将 received 重置为 0，调用方必须重新传输，
不会补零冒充原文件。过期和取消清理涵盖上传复制的暂存文件；内容寻址 blob 由引用感知 GC 处理。

### 3.3 模板与字段定义

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/templates` | write | 上传模板（DOCX/XLSX/PDF/PPTX/PNG/JPEG，旧格式自动转换并提示） |
| POST | `/templates/builtin` | write | 程序化创建 DOCX/XLSX 模型与真实模板文件，支持幂等 |
| GET | `/templates/{tid}/builtin` | read | 导出可移植 editor 模型，可带 version |
| PUT | `/templates/{tid}/builtin` | write | base_version 并发检查；保存新版本，支持幂等；`note`（≤200 字）为这个版本的说明（命名检查点），留空即「在编辑器中保存」；编辑器里有尚未保存为版本的实时修改时 409 `live_edits`（与恢复相同的 `detail`），`discard_live: true` 表示确认替换并丢弃它们（CI-113）。部署对模板所在的工作空间打开 `SYNWEVO_SHEET_LIVE`（`on` 或试用名单里有它）时，表格模板正在进行的实时协同会把这个版本对账进去（同一对象以保存的版本为准，并通知被覆盖的人），而不是整份替换；契约不变（[docs-sheet-live.md](../platform/docs-sheet-live.md) §9） |
| GET | `/templates` | read | 模板列表 |
| GET | `/templates/{tid}` | read | 详情：字段、版本历史、可用导出格式 |
| GET | `/templates/{tid}/fields` | read | 导出字段定义（`docweave.template-fields` v1） |
| POST | `/templates/{tid}/fields` | write | 按定义创建字段（每次调用产生一个新模板版本） |
| PATCH | `/templates/{tid}/fields/{field_id}` | write | `{base_head, name?, required?, data_type?, format?, default?, font_size?, min_font_size?, align?, valign?, empty_policy?, placeholder?, binding?, columns?, overflow?, …}`：修改一个字段（与工作台同一服务，产生一个新版本）；`base_head` 必填，与当前 head 不符 409 `stale_template`（`details.head` 为当前值）——对已成功的请求重发也得到它，不会改两次；其他属性 400 `invalid` |
| DELETE | `/templates/{tid}/fields/{field_id}?base_head=&project_id=&cascade=` | write | 删除一个字段（新版本）；给出 `project_id` 时同时移除该项目中只绑定这个字段的映射（`removed_mappings`）；内置编辑器模板 409 `builtin_fields`（字段在编辑器里维护） |
| GET | `/templates/{tid}/content?which=original\|working` | read | 下载原始模板或含字段的工作副本 |
| DELETE | `/templates/{tid}` | admin | 删除模板 |
| GET | `/templates/{tid}/usage` | read | 使用此模板的项目：跟随当前版本的与固定了版本的（恢复前的影响说明） |
| POST | `/templates/{tid}/restore` | write | `{version, base_head?, discard_live?}`：以该版本的内容新建当前版本，历史保留；`base_head` 不符 409 `stale_template`；编辑器里有尚未保存为版本的实时修改时 409 `live_edits`（`detail.online` 在线编辑者、`detail.last_by` 最后修改者），`discard_live: true` 表示确认丢弃 |
| GET | `/organization-templates?include_deprecated=` | read | 当前空间所属组织的共享模板（offset/cursor 分页；个人空间 `organization=null`）。**需委托令牌**：组织成员身份属于人，服务账户/API 密钥答 403 `delegated_required` |
| GET | `/organization-templates/{pid}` | read | 共享模板详情与发布历史（需委托令牌） |
| POST | `/templates/{tid}/publish` | write | 把本空间模板发布到组织：冻结 head 版本为第 1 次发布；源空间编辑者；支持幂等（需委托令牌） |
| POST | `/organization-templates/{pid}/releases` | write | 更新发布：源模板 head 有新版本则产生新一次发布，或只改说明；维护者（需委托令牌） |
| POST | `/organization-templates/{pid}/deprecate` | write | `{deprecated, note}` 弃用/恢复；维护者（需委托令牌） |
| POST | `/organization-templates/{pid}/use` | write | 把当前发布复制成本空间的模板（`origin` 记录发布），可 `project_id` 同时绑定；支持幂等（需委托令牌） |

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
| `repeat` | XLSX / DOCX / PPTX | 明细模板行：XLSX `sheet`+`row`+`c1`+`c2`；DOCX `table`+`row`(+`header_rows`)；PPTX `slide`+`shape`+`row`；可带 `empty_policy`、`placeholder`；PPTX 另可带 `overflow`：表格超出幻灯片底部时 `warn`（缺省，照常生成并警告）/ `error`（该份失败）/ `continue`（复制本张幻灯片续写：表头行每张重复，明细下方的行只在最后一张，幻灯片上其他字段同样填写；按模板行高计算；含图表、SmartArt、音视频或嵌入对象的幻灯片不复制） |
| `repeat_block` | DOCX / XLSX | DOCX：连续段落块，`first_find`/`last_find` 或 `first`/`last` 路径，另可带 `auto_columns`；XLSX：整行区段，`sheet`+`rows`（Excel 行号，如 `"3:6"`，至少两行），块内的 `repeat` 行成为它的明细（每份只写入该段的行）；都可带 `separator`(`none`/`blank`/`page_break`)、`empty_policy`(`remove`/`placeholder`/`error`)、`placeholder` |
| `block_column` | DOCX / XLSX | 给已存在的内容块加一列：`block`(名称或 id) + DOCX 的 `find` 或 `path`+`start`+`end`；XLSX 的 `cell`（块内、不在明细行上的单元格） |
| `pdf_box` | PDF / 图片 | `page`+`rect`（页面坐标，单位点）、`nrect`（0–1 比例）或 `find`；`font_size`、`min_font_size`、`align`、`valign`、`color`、`clear` |
| `pdf_repeat` | PDF / 图片 | 同上定位 + `layout`（列与行高，可省略由服务识别）、`overflow`(`error`/`continue`/`shrink`)、`empty_policy` |
| `pdf_field` | PDF 表单 | `field`（AcroForm 字段名） |

内置编辑器模板也可经 `/templates/builtin` 创建，并经 `/templates/{tid}/builtin` 读回和保存版本；
完整模型、字段与限制见 [editor-models.md](editor-models.md)。

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
| POST | `/projects/{pid}/preflight` | read；simulate 需 write | 样本数、错误/警告、预计输出；`simulate:true` 或有上限的整数会实际试生成，`simulation.limited` 指明未覆盖样本 |
| GET | `/projects/{pid}/preflight/issues` | read | 按级别过滤的逐样本、逐字段问题明细，带分页和来源位置 |
| POST | `/projects/{pid}/preview` | write + 当前导出权 | `render_project_preview`：原生文件下载，复用 UI/任务引擎与读回校验，不生成正式任务或产物 |
| POST | `/projects/{pid}/jobs` | write | 提交任务（202）。头 `Idempotency-Key` 或 body `idempotency_key` |
| GET | `/projects/{pid}/jobs` | read | 项目的任务列表 |
| GET | `/jobs/{jid}` | read | 状态与计数 |
| GET | `/jobs/{jid}/items` | read | 逐样本结果，`status` 过滤、`offset`/`limit` 分页 |
| GET | `/jobs/{jid}/runs` | read | 运行记录（初次生成与每次重试） |
| GET | `/jobs/{jid}/events?after=<seq>` | read | 事件流（轮询），`next_after` 用于下一次请求 |
| POST | `/jobs/{jid}/cancel` | write | 取消 |
| POST | `/jobs/{jid}/retry` | write | 默认重试失败/取消项；`failed_only:true` 只重试失败项；可选 `include_skipped`，与 failed_only 互斥，严格布尔值 |

任务提交体：`{"formats": ["docx","pdf"], "naming": "{客户编号}_{客户名称}", "error_policy": "block|skip", "layout": "off|warn|fit", "keep_fields": false}`。

`layout` 是分页检查（仅对 DOCX / PPTX 这类有分页的格式生效，需要 LibreOffice）：

| 值 | 行为 |
|---|---|
| `off`（默认） | 不检查。新内容更长时输出页数可能与模板不同 |
| `warn` | 每份输出实际排版一次并数页数；超出模板页数的样本仍生成，但在结果清单里带 `page_overflow` **警告**（含实际页数与模板页数） |
| `fit` | 同上，但超出的样本判为**失败**不产出；若字段勾选了「超长时允许缩小字号」，先按 92%/84%/76%/68%/60% 逐级缩小并每级重新排版，放得下则成功并带 `shrunk_to_fit` 警告。PPTX 的明细表格超出幻灯片底部（幻灯片数不变）同样判为失败；续写增加的幻灯片按页数超出处理 |

省略 `layout` 时沿用项目设置；传入未列出的值返回 400 `bad_layout`。LibreOffice 不可用时返回 `layout_unavailable` 警告而不是假装检查过。

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

**幂等**：同一主体 + 同一租户 + 同一操作作用域 + 同一 `Idempotency-Key`：
内容相同 → 返回同一个任务并带 `"idempotent_replay": true`；内容不同 → `409 idempotency_conflict`。
键的保留期默认 24 小时（`SYNWEVO_DOCS_IDEMPOTENCY_TTL_HOURS`），过期后同一个键会创建新任务。并发提交由数据库唯一约束裁决，只会产生一个任务。

**快照**：任务创建时固定来源文件版本、模板版本、字段、映射、规则与生成参数；之后在界面或 API 修改任何内容都不影响已提交的任务。

**重试**：默认沿用原任务快照，作为该任务的新一次「运行」记录（`/runs`），不会覆盖已成功的产物。若输入、模板或规则已变化，请重新预检并提交新任务（新任务与旧任务各自独立）。
`failed_only:true` 保留取消、跳过和成功项；无失败项时返回 `409 nothing_to_retry`。改变重试选择却沿用同键返回冲突。

**预览**：JSON 体仅接受 `index`（非负整数）或 `key`（不可同时提供），以及可选 `signature`。
返回原生二进制附件及 `X-Content-SHA256`、`X-Project-Signature`、`X-Template-Version`、`X-Record-Key`。
超范围/未知样本明确拒绝；签名变更为 409 `stale`，阻断问题为 409 `preview_blocked`。
渲染结束再次校验当前凭据、导出权及版本；预览缓存不是绕过撤权的入口。

**恢复**：任务由工作线程以租约（默认 30 秒，`SYNWEVO_DOCS_JOB_LEASE_S`）持有并定期续约。进程被杀、线程异常退出后，租约过期（同机进程已消失时立即判定）会自动把未完成样本放回队列继续；已发布的产物不会重复生成。不承诺「恰好一次执行」，但产物发布是原子的（产物与样本状态同一事务提交）。

### 3.6 结果与下载

内置编辑器模型、节点、坐标与完整请求示例见 [editor-models.md](editor-models.md)。

普通创建请求的幂等记录、业务数据库写入与成功响应在同一个 Store 事务内提交。提交前失败或进程中断会回滚；
并发同键请求等待提交后重放，并继续检查当前对象权限。不可变 blob 字节和临时文件不属于 SQLite 事务，
这里不声称整个文件系统具有数据库回滚语义。旧版本留下的无响应 claim 无法证明业务未执行，超过诊断窗口后
返回 409 `idempotency_outcome_unknown`；禁止盲目换键重试，需先核对原操作结果。后台清理不会删除这种不确定记录。

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| GET | `/jobs/{jid}/artifacts` | read | 产物清单：`filename`、`format`、`size`、`sha256`、所属样本 |
| GET | `/artifacts/{aid}/content` | read | 下载单个产物（`Content-Disposition`、正确 MIME、`X-Content-SHA256`） |
| POST | `/artifacts/{aid}/link` | read | 生成有时效的下载链接（默认 15 分钟，`SYNWEVO_DOCS_DOWNLOAD_LINK_TTL_S`） |
| GET | `/jobs/{jid}/archive?format=docx` | read | 全部产物 ZIP（含「生成清单.csv」）；服务端写临时文件后流式返回 |
| POST | `/jobs/{jid}/archive-link` | read | ZIP 的有时效链接 |
| GET | `/jobs/{jid}/report.csv` | read | 生成清单（逐样本状态、输出文件、来源位置、问题说明） |
| GET | `/download/{token}` | 链接本身即凭据 | 上述链接的下载入口；过期返回 `410 link_expired`，重新调用 `/link` 即可 |

产物被删除后返回 `410 artifact_deleted`；产物文件内容按 SHA-256 内容寻址存储，可用返回的 `sha256` 校验下载完整性。
表中所有文件内容、链接、ZIP 和报告操作额外检查当前导出权限；新链接关联签发主体，撤权后立即拒绝新下载请求。
**兼容性收紧**：旧的不含签发主体的链接返回 `403 link_revoked`；使用仍有效的凭据重新请求 `/link`，不需要更换身份密钥。

**链接里的域名**：`/link` 与 `/archive-link` 返回的 URL 默认取 `SYNWEVO_PUBLIC_URL`；未设置时按
仅当 `SYNWEVO_TRUST_PROXY` 明确配置可信跳数时才使用 `X-Forwarded-Proto` / `X-Forwarded-Host`，从右向左选对应跳；否则使用直接请求源。放在反向代理之后设置
`SYNWEVO_PUBLIC_URL=https://你的域名`，或配置可信跳数并由代理规范转发头部。两者都没有时，链接会带上
代理到源站那一跳的协议和地址——典型结果是 `http://127.0.0.1:8765/...`，调用方拿到后连的是自己的机器。
链接本身即凭据，因此协议写成 `http://` 不只是不好看。令牌的校验与 URL 怎么拼无关，已签发的链接不受影响。

## 4. 事件与 Webhook

事件结构（`/jobs/{jid}/events` 与 Webhook 共用）：

```json
{ "seq": 12, "id": "ev_…", "type": "job.completed", "version": 1, "job_id": "j_…",
  "occurred_at": 1758592000.12, "data": { "status": "completed", "run_no": 1, "counts": {...} } }
```

类型：`job.created`、`job.started`、`job.planned`、`job.cancel_requested`、`job.retry_requested`、`job.recovered`、
`job.completed`、`job.completed_with_errors`、`job.failed`、`job.cancelled`。`seq` 在同一任务内单调递增（轮询用 `after`）。

**来源文件事件（API-2）**：上传的来源文件得出接纳结论时各发一次，与状态变化在同一事务里写入（数据库触发器，覆盖上传、
共享字节隔离、运维补扫、OCR 与重新解析各条路径），不会出现状态已变而事件丢失：

| 类型 | 何时 | `data.reason` |
|---|---|---|
| `file.ready` | 解析完成可用；`needs_ocr` 经 OCR 后也会再发一次 | 安全检查结论（如 `clean`、`not_configured`） |
| `file.needs_ocr` | 可读但页面没有文字层，映射前需先 OCR（`POST /files/{fid}/ocr`） | 同上 |
| `file.rejected` | 被安全检查拒绝：恶意软件（`status=rejected`）、设有密码或读不完（`status=error`） | `infected` / `encrypted` 等 |
| `file.failed` | 已接纳但无法解析 | 同上 |

负载只有 `data: {file_id, project_id, status, reason, version}`、`job: null`，不含文件名与内容；需要更多信息请用凭据读取
`/files/{fid}`。扫描暂不可用（`scan_failed`，自动重试）期间不发事件。订阅可用 `file.*` 或具体类型过滤；
`GET /events?types=file.ready,…` 同样可查，条目带 `file_id`、`project_id`，`job_id` 为 null。

Webhook 默认关闭，按部署配置开启：

```bash
SYNWEVO_DOCS_WEBHOOK_URLS=https://automation.example.com/hooks/docweave
SYNWEVO_DOCS_WEBHOOK_SECRET=<共享密钥>
SYNWEVO_DOCS_WEBHOOK_EVENTS=job.completed,job.completed_with_errors,job.failed,job.cancelled
# 隔离本机测试只显式允许指定接收器主机；默认不开放私网
```

* 工作空间订阅由 `/webhooks/subscriptions` 管理，需管理 scope 与空间治理资格；支持版本条件更新、暂停、恢复、轮换、投递查询、受控重投。恢复订阅只接收恢复之后的事件。
* 推荐 Standard Webhooks 头：`webhook-id`、`webhook-timestamp`、`webhook-signature`。签名为原始字节上的 HMAC-SHA256，消息为 `<id>.<timestamp>.<body>`，签名编码为 `v1,<base64>`；轮换重叠期可带多个签名。部署级回调另保留外部兼容头 `X-DocWeave-*`，不能随品牌改名。
* 接收端校验原始字节、时间偏差和事件 ID；成功处理或持久入队后才返回 2xx。重复和乱序是正常情况，去重必须与业务处理配合，参考 [真实接收器示例](../../apps/docs/examples/webhook_receiver.py)。
* 默认首次失败后最多 9 次重试：5、300、1800、7200、18000、36000、50400、72000、86400 秒，带 ±20% 抖动；429/503 尊重有上限的 Retry-After。410 停用订阅。投递记录保存实际状态、尝试和关联 ID。
* DNS、连接、TLS 与响应头共用默认 15 秒总期限；每轮有界、公平访问不同接收器。暂停/撤销及恢复栅栏在发送前重查，在途结果不能复活已丢弃的投递。
* 创建订阅、轮换签名密钥、ping、重投暂不提供持久幂等，携带 `Idempotency-Key` 明确返回 `400 idempotency_not_supported`；不要对这些操作自动重试造成重复副作用。
* 安全：默认要求 https；回调地址每次投递前解析，解析到内网地址时拒绝，除非在 `SYNWEVO_DOCS_WEBHOOK_ALLOW_PRIVATE` 白名单中；不跟随跳转。
* 回调失败不影响产物查询与下载。

## 5. 限制与约定

### 5.1 流量与容量策略（平台管理员）

三类限制含义不同，分开设置、分开报告：

| 限制 | 含义 | 超出时 | 可设置于 |
|---|---|---|---|
| `credential_rpm` | 单个凭据每分钟请求数（按密钥谱系/服务账户计） | 429 `rate_limited`，`limit_scope=credential`，带 `Retry-After` | 平台、工作空间（作为其凭据的默认）、单个凭据 |
| `workspace_rpm` | 工作空间全部机器凭据合计每分钟请求数 | 429 `rate_limited`，`limit_scope=workspace`，带 `Retry-After` | 平台、工作空间 |
| `active_jobs` | 工作空间同时排队或进行的生成任务数（网页与 API 同一上限） | 429 `queue_full`，`details.limit`、`details.policy_source`；任务结束后才有空位 | 平台、工作空间 |

取值顺序：凭据 → 工作空间 → 平台 → 部署默认值（环境变量）；留空即继承。管理台接口（仅平台管理员的
`api_platform.manage`，写操作需近期重新验证与 8–500 字理由，全部写入审计）：

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/admin/api-platform/quotas` | 部署默认值、平台设置与生效值、已单独设置的策略（分页）、各字段范围 |
| GET | `/api/admin/api-platform/quotas/effective?workspace_id=&credential=` | 某工作空间（及其凭据，如 `api_key:<谱系>`、`service_account:<client_id>`）每项的生效值、来源层与此刻用量 |
| POST | `/api/admin/api-platform/quotas/preview/{platform\|workspace/<id>\|credential/<kind>:<id>}` | 预演：修改前后生效值与对进行中工作的影响；不写入 |
| PUT | `/api/admin/api-platform/quotas/{同上}` | `{base_version, credential_rpm?, workspace_rpm?, active_jobs?, reason}`；`base_version` 与当前不符 409 `stale_policy`（附当前值）；下一次请求即生效 |
| GET | `/api/admin/api-platform/quotas/history/{同上}` | 每次修改的前后值、版本、操作者与理由 |

降低上限不会停止进行中的任务；清除覆盖保留版本号（旧页面不能静默撤销较新的修改）。调用记录
（`/api/admin/api-platform/calls`）可按请求编号、凭据或客户端、接口、状态类别与错误代码查询，被网关拒绝的
请求同样记在其接口模板下（从不记录原始路径、令牌、查询或正文）。

* 分页：`offset` + `limit`，响应含 `total`、`next_offset`（`null` 表示到底）。
* 文件大小上限 `SYNWEVO_DOCS_MAX_FILE_MB`（默认 100 MB）；单任务样本上限 `SYNWEVO_DOCS_MAX_RECORDS_PER_JOB`（默认 20 000）。
* 数据保留：产物与来源文件保存在 `SYNWEVO_DATA_DIR`，不自动过期；清理策略见 [../operations.md](../operations.md)。
* 兼容性：`/api/v1` 路径与字段只增不删；新增字段可能随时出现，客户端应忽略未知字段。定义文档（`docweave.*`）带 `version`，不兼容版本会被拒绝并说明。界面内部接口（不含 `/v1`）不属于兼容承诺范围。
