# 设计稿：服务账户调用业务应用（Forms / Tables / DataPrep / Extract / Approvals）

状态：**设计稿，未实现**。2026-10-03，WP9 的后续工作包，待协调者确认后再动代码。
前置：`/api/suite/v1` 目前只接受用户委托（[`../integration/suite-api.md`](../integration/suite-api.md)）；服务账户在登记
（`suite_delegation_only`）和网关（`service_account_not_supported`）两处被拒绝。本文说明为什么不能简单放开，以及放开需要
改什么、按什么顺序、哪些永远不放开。

代码事实来自 2026-10-03 对 `feat/business-apps` 的实读（一次独立只读审计 + 本人复核下文引用的每一处）；行号会漂移，
以函数名为准。标注“待核实”的未逐行复核。

## 1. 问题：授权不在一个地方

`Capabilities.invoke` 为“一个人”执行操作：`authorize()` 取用户行，再取其在目标工作空间的实时成员角色。如果只有这一处，
给服务账户换一个授权分支就够了。实际上各应用在 `invoke` **之外**又按 user 行与成员关系重新推导了十余次，放在 `Call` 上的
任何“由 scope 推导的角色”都会被覆盖或忽略：

| 位置 | 做了什么 | 对没有 user 行 / 成员行的主体 |
|---|---|---|
| `Capabilities.authorize` / `check_workspace` | 用户行、应用资格、成员角色 | 401 `account_unavailable` |
| `Capabilities.invoke` 事务内二次 `authorize` | 覆盖 `call.user` / `call.role` | 同上 |
| `Capabilities._check_result`、`run`、`present_run` | 重放与回执按 `op_runs.user_id` 重新鉴权 | 幂等重试得到 409，回执被隐藏 |
| `Tables.role`（每个处理函数入口） | `check_workspace(c.user_id, …)`，不看 `call.role` | 全部 403（失败即关闭） |
| `Tables.export_right`、`Extract.export_right`、DataPrep 导出路由 | 成员行的 `can_export` | 403 |
| `Approvals._auth`（每个处理函数入口） | 再次 `authorize` 并覆盖 `call.user/role` | 全部 401 |
| `Approvals._eligible` | 审批人必须是活跃用户且有写权限的成员 | 服务主体永远不能成为审批人（正确） |
| `Extract.auth` 与 worker `check()` | 用户行 + 成员角色，按 `extract_tasks.created_by` | `task.create` 被接纳后在 worker 第一步失败 |
| DataPrep `check_sources` / `result_guard`、worker `checkpoint` | `authorize(…, created_by, …)` | 涉及数据集与运行的操作全部失败 |
| Forms `create_request` / `request_changes` / `accept` | `call.user.get("display_name") or call.user["email"]` | KeyError → 500 |
| Forms 提交后通知请求创建者 | 以 `created_by` 为收件人 | 写出无人接收的通知行，稍后被标为跳过 |
| `Capabilities._audit` | `actor_kind` 写死 `"user"` | 机器调用被记成人（表本身允许 `machine`） |
| `authority._check_service` | scope 写死 `docweave.*`，`app_id` 必须等于客户端的应用 | 不能用于 Suite 的延后工作 |

存储不是障碍：五个应用的 schema 都没有指向 `users` 的外键，执行者列是 `TEXT`，`op_runs.user_id` 只是 `NOT NULL`。

结论：把 `call.user` 伪造成 `{"id": "service:<client_id>"}` 在任何一个应用上都不成立——Tables、Approvals 和大部分 DataPrep
直接拒绝；Extract 与 Forms 会接纳后失败或行为错误；还有两条为“人”写的规则会变成空规则（§5）。

## 2. 主体感知的统一授权

**一个函数回答“这个主体现在能在这个工作空间对这个操作做什么”，所有调用点都走它。**

```python
# synwevo_core.capabilities
@dataclass(frozen=True)
class Actor:
    kind: Literal["user", "service_account"]
    id: str                 # 用户 ID，或 client_id
    label: str              # 记录与界面显示：显示名/邮箱，或「<客户端名>（应用）」
    workspace_id: str | None
    role: str | None        # user: 实时成员角色；service_account: None
    grants: frozenset[str]  # service_account: 当前有效的 scope；user: 空

class Capabilities:
    def admit(self, op: Operation, principal: dict, workspace_id: str | None) -> Actor: ...
    def allows(self, actor: Actor, app_id: str, need: str) -> ServiceError | None: ...
```

* `principal` 是 `authority.describe()` 的描述符（`user` / `delegated` / `service_account`），不是 user_id。
* **人**（`user`、`delegated`）：现有逻辑原样——用户行、`Apps.check_access`、成员角色、`role_refusal`。
* **服务账户**：`Apps.check_access_for_workspace(ws, app)`（已存在，Docs 的机器网关在用；能力层没有调用它）；客户端仍启用、仍是 `service`、绑定的
  工作空间等于目标空间；所有者审批仍有效（`api_platform.Approvals.active`，按 `client_id` 绑定）；所需 scope
  （`machine_scope(op)`）在“登记 ∩ 审批 ∩ 令牌”的交集里。**没有角色**：`need` 由 scope 回答——`read`→`<app>.read`，
  `write`→`<app>.write`，`manage`→拒绝（见 §6）。
* `role_allows(role, need)` 的所有调用点改为 `caps.allows(actor, app, need)`；`Call.can()` / `Call.require()` 随之改。

必须改为经由 `admit` / `allows` 的调用点（即 §1 表的前十行）：`Capabilities.authorize`（保留为人的薄封装）、事务内复核、
`_check_result` / `present_run`、`Tables.role`、`Tables.export_right`、`Approvals._auth`、`Extract.auth`、
`Extract.export_right`、`dataprep/history.py` 的两处 `authorize`、`dataprep/service.py` worker 的 `authorize`、
DataPrep 路由的 `authorize`。导出权（把数据带出平台）对服务账户**不由角色推导**：需要单独的 `<app>.export` scope，或在第一版
直接不开放导出（建议后者，见 §7）。

一条机械检查应随实现一起加入：`apps/suite` 内除 `capabilities` 外不得出现 `identity.membership(` 与
`check_workspace(`——否则下一个新处理函数会再写一处绕开统一授权的成员查询。

## 3. `Call` 携带主体

```python
@dataclass
class Call:
    actor: Actor                    # 新增：谁在做
    user: dict | None               # 仅当 actor.kind == "user"；服务主体为 None
    ...
    @property
    def actor_id(self) -> str:      # 写入 created_by 等列的值：用户 ID，或 "service:<client_id>"
    @property
    def user_id(self) -> str:       # 保留给“必须是人”的代码路径；服务主体访问时抛出 person_required
```

* 写入执行者列的地方（约 30 处，逐表见审计）从 `call.user_id` 改为 `call.actor_id`。`authority.label()` 已经给出
  `service:<client_id>` 这个形式（Docs 任务就这样记），沿用它，读回时不需要解析。
* `user_id` 对服务主体**抛出而不是返回伪 ID**：把“默认当成人”的代码变成显式失败，而不是静默的错误行为。每一处仍读
  `call.user_id` 的代码都必须回答“这里为什么必须是人”。
* Forms 的三处 `call.user["email"]` 改为 `call.actor.label`。
* 以调用者为收件人的通知（Forms 提交通知请求创建者、Approvals 提醒发起人）：收件人解析改为“`actor_id` 是用户才通知”；
  服务主体创建的请求不发站内通知，改由事件送达（依赖事件订阅，见 §7）。不得写出无人接收的通知行。
* `_audit`：`actor_kind` 取自 `actor.kind`（`user` / `machine`），`actor_id` 为 `actor_id`，`meta` 记录审批 ID 与
  `approved_by`（目录接口 `workspace_api` 已这样做）。
* `op_runs.user_id`：存 `actor_id`（列是 `NOT NULL TEXT`，无外键）；幂等唯一键 `(user_id, op, workspace, key)` 因而对服务
  主体按 `client_id` 隔离——轮换密钥不换命名空间，符合现有约定。
* 读回执行者 ID 并显示的地方（Tables 历史的 `actor_name`、Approvals 详情的 people 字典）：对 `service:` 前缀返回客户端
  名称与 `kind: "service_account"`，界面显示为“应用：<名称>”，三语言。

## 4. 延后工作的描述符

用户委托已经做完（`Capabilities.standing` / `resumed`，提交 66e057e）：worker 在每个复核点按 `op_runs.principal` 复核发起它的
授权。服务账户沿用同一入口，补一个分支：

* `standing()` 对 `kind == "service_account"`：客户端仍启用、仍绑定该空间；所有者审批仍有效且仍覆盖所需 scope
  （`api_platform.approval_failure` 的 Suite 版——现有函数把 scope 写死为 `docweave.*`，需要按资源取 scope）；工作空间与
  应用仍可用。返回当前有效 scope，作为该步骤读取其他应用来源的上限。
* worker 里“再按人复核一次”的那一步（`authorize(…, created_by, …)`、`Extract.auth(created_by)`、
  `Approvals._eligible(waits.user_id)`）改为 `caps.admit(op, stored_principal, workspace)`：**延后工作按存储的主体复核，
  不按 `created_by` 列猜**。这也修正了今天的一个不对称——页面发起的运行按 `created_by` 复核，而描述符其实已经在
  `op_runs.principal` 里。
* `approval_waits.user_id`：服务主体的等待以 `run_id` 找回描述符；列里存 `actor_id`。
* 不改 schema：`op_runs.principal` 已是完整描述符。唯一要加的是 `authority.describe()` 对 `service_account` 已有的形状
  （`client_id`、`tenant`）再带上 `approval_id`，使审批被替换后旧运行可被识别（待定：是否需要）。

## 5. 审批：机器发起人的规则

两条为人写的规则，对机器发起人会失效或变空：

1. **“发起人不得审批自己的申请”**比较的是审批人 ID 与 `requester_id`。服务主体永远不会等于某个人，于是持有服务凭据的人
   可以通过凭据发起申请，再以本人身份批准——职责分离变成空规则。
2. **“发起人不得修正审批人”**（`repair`）是同一比较，同样变空。

规则（需要协调者 / 产品确认，建议按此实现）：

* 服务账户**可以**：发起（`request.create`）、修订与撤回自己发起的申请、查询、添加意见、等待结果。
* 服务账户**永远不可以**：`request.decide`、`request.transfer`、`request.repair`——没有人的主体不能做人的决定；
  `<app>.decide` 不登记为服务 scope，`machine_scope` 对服务主体在这三个操作上返回 None。`Approvals._eligible` 已保证服务
  主体不可能被指派为审批人。
* **机器发起的申请，排除“控制该凭据的人”**：申请记录 `requester_kind = "service_account"`、`requester_client`，以及发起
  时该凭据的**审批所有者**（`api_platform_approvals.approved_by`）与**申请该凭据的管理员**（`requested_by`）。这两个人对该
  申请没有审批资格（并入 `_excluded(r)`），理由与“发起人不得自批”相同：他们能决定这个凭据发起什么。
* **模板显式选择是否接受机器发起**：`definition.machine_requests: "refuse" | "accept"`，默认 `refuse`。金额、合同类模板
  不应因为平台新增了一种调用方就默认接受无人发起的申请；发布新版本时由模板管理者决定。
* 提醒与升级不发给机器发起人（§3 的通知规则）；`request.list` 的 `sent` / `handled` 视图对服务主体按 `actor_id` 过滤，
  `todo` 恒为空——默认视图对服务主体改为 `all`，否则是“静默的空列表”。

Extract 的同类问题：`task.correct` / `task.confirm` 是下游（`dataprep.dataset.from_extract`）信赖的**人工复核**。服务主体
不得写入确认修订；`extract/service.py::_review` 的 `via not in ("ui","api")` 门改为按主体种类判断（`actor.kind == "user"`），
不再用 `via` 代表“有人在场”。

## 6. 哪些开放，哪些不开放

| | 用户委托（已实现） | 服务账户（本设计） |
|---|---|---|
| 只读操作 | `<app>.read` | `<app>.read` |
| 写操作（`need=write`） | `<app>.write` + 本人角色 | `<app>.write` |
| 管理操作（`need=manage`：表结构、审批模板、Agent 空间策略） | `<app>.write` + 本人为所有者/管理员 | **不开放**。结构与模板治理属于人；确有需要时另设 `<app>.manage` 并要求所有者单独审批 |
| 人的决定（`unattended=False`） | `<app>.decide` | **永不** |
| 个人操作（Agent、预算、同意、报价） | 不可达 | 不可达 |
| 跨工作空间写入 | 不可达 | 不可达 |
| 导出（把数据带出平台） | 本人的导出权 | 第一版不开放（§7） |

凭据模型沿用目录接口（API-5）：所有者审批 → 平台管理员签发 → 每次调用复核审批。`api_platform.resource_scopes()` 增加
Suite 资源；一次审批一个资源；`Clients.register` 放开 `api_scopes` 里的 Suite scope（仅 read / write）。

## 7. 顺序与验证

每一步都是可独立合并、可独立回退的提交；前一步不通过不做后一步。

1. **核心**：`Actor`、`admit` / `allows`、`Call.actor`、`_audit`、`_check_result`。行为对人不变——以现有全部套件为准
   （当前 `apps/suite/tests` 620 项）。不开放任何服务入口。
2. **逐应用迁移调用点**，按风险从低到高：Forms（三处标签 + 通知）→ Extract（`auth`、worker、`_review` 的主体判断）→
   DataPrep（`check_sources`、`result_guard`、worker）→ Tables（`role`）→ Approvals（`_auth`、§5 的规则与模板字段）。
   每个应用一个提交，各带“人的行为不变”的回归和下一步要用的服务主体测试夹具。
3. **凭据**：审批资源、登记、`verify_suite` 的服务分支、`standing()` 的服务分支。到这一步服务账户才第一次能调用。
4. **契约与 SDK**：`x-synwevo-identity` 按操作列出可用主体；`SuiteClient` 接受 `ClientCredentials`。

反向测试（独立于实现的期望）：

* 每个应用：只读凭据不能写；无审批、审批过期、审批人不再是所有者、客户端停用、空间冻结、应用停用——下一次调用即拒绝。
* 服务主体对 `need=manage`、`unattended=False`、个人操作、跨空间写入：全部拒绝且不产生任何行。
* 审批：机器发起 → 凭据的审批所有者与申请者不能批准；其他合格成员可以；模板为 `refuse` 时发起被拒。
* 延后工作：审批撤回 / 客户端停用发生在排队中与处理中途——不再写入（`test_machine_deferred.py` 的服务主体版本）。
* 人的全部既有行为：现有套件不改断言全部通过。
* 机械检查：`apps/suite` 内不再出现直接的成员查询。

不在本包内、但会被它用到的：文件上传/下载的机器路由（WP9 发现 5，正在做）、事件订阅（服务主体的通知替代）、导出
scope。导出在这三者落地前不对服务账户开放。

## 8. 需要确认的决定

1. §5 的排除规则：凭据的审批所有者**和**申请它的平台管理员都排除，还是只排除所有者？
2. 模板 `machine_requests` 默认 `refuse`（建议）还是 `accept`？
3. 管理操作对服务账户永不开放（建议），还是预留 `<app>.manage`？
4. 服务主体创建的记录在界面上显示为“应用：<客户端名>”——是否需要同时显示批准它的所有者？
5. §2 的机械检查放进 `check_boundaries.py`，还是单独脚本？
