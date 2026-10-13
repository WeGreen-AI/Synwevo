# 业务应用与个人 Agent：有影响的决定

每条写明决定、依据（现场事实）、代价与撤回条件。现场事实的来源是 2026-09-29 首轮调查（代码与配置，不是旧记录）。

## D1 六个新应用落在哪里：一个新模块 `apps/suite`，挂在 workplace 源（portal 角色）下

* **决定**：Python 包 `synwevo_suite`（`apps/suite/`），前端外壳 `@synwevo/suite-web`（`apps/suite/web`，静态目录
  `suite-assets/`）。由 `synwevo_server` 在 `portal` 与 `all` 角色挂载。入口路径：`/forms`、`/tables`、`/dataprep`、
  `/approvals`、`/extract`、`/agent`，正式地址即 `https://workplace.synwevo.com/<app>`。每个应用是 `synwevo_suite`
  下的独立子包（`forms/`、`tables/`…），**兄弟子包互不导入**（`check_boundaries.py` 机械检查），只经
  `synwevo_core.capabilities` 注册表互相调用；共用代码只能放 `synwevo_suite.common`。
* **依据**：生产 nginx 按主机把 `workplace.synwevo.com` 的全部路径转给 portal 容器；`nginx.conf`、`compose.yaml`
  属主机、CI 部署不下发（`receive.py` 只构建镜像并 `compose up`）。新容器或新子域都需要运维改主机配置与 DNS，
  主提示词 §1.2 要求不为“独立”引入 DNS 依赖。portal 角色已有统一登录（`portal-web` 客户端）。
* **代价**：portal 容器 `mem_limit 768m / 1 CPU`（主机 compose），DataPrep/Extract 必须流式与限额。
  六个应用共进程——一个应用的崩溃会影响同进程的其他应用；以线程池隔离长任务、限额与超时缓解。
* **撤回条件**：出现独立扩缩容需求时，按角色拆出 `suite` 容器，代码不变，只改组装与主机配置。

## D2 共享契约：`synwevo_core.capabilities`（能力注册 + 运行记录 + 事件出站表）

* **决定**：应用在 `install` 时登记清单（应用 ID、名称、入口、对象类型）与操作（稳定 ID、Pydantic 输入/输出模型
  → JSON Schema、所需权限、副作用级别、同步/异步、可取消/可重试、幂等策略、Agent 暴露策略、人工决定限制）。
  所有调用入口（界面、`/api/ops/{op}`、流程步骤、Agent 工具）走同一个 `invoke()`：校验输入 → 解析目标空间 →
  应用资格与空间角色 → 幂等 → 写 `op_runs` → 执行处理函数 → 结果/错误 → 同一事务写 `app_events`。
  运行记录携带 `principal`（`synwevo_core.authority` 的描述符，含委托来源）、`workspace_id`、`app_id`、
  `operation`、`config_version`、`correlation_id`、`parent_run_id`。
* **依据**：现有 `/api/v1` 与幂等、事件、Webhook 全部硬编码为 Docs（`MachineAuth._platform_token` 拒绝非 Docs
  客户端，`authority.check` 硬编码 `docweave.*` scope）；没有通用编排。Pydantic 已随 FastAPI 在依赖里。
* **工作空间由请求显式给出**：每次调用带 `workspace_id`，由服务端按实时成员关系核验；不读浏览器标签页绑定
  （`X-Synwevo-Workspace`）来决定写入位置。

## D3 Docs / Message 通过注入的适配器加入契约

* **决定**：沿用 `DocsControlPlane` / `DocumentIngest` 的组装层注入模式：`synwevo_server` 在 portal 进程里构造
  Docs 适配器（私有、不挂载的 Docs ASGI 应用，`start_jobs=False`），向注册表登记 `docs.*` 操作；执行仍由 Docs
  自己的业务路由与作业执行器完成，权限按调用人实时计算。Message 的业务层在 core，`message.*` 操作直接调用。
  Extract 的文件读取（文字层、OCR 单词坐标、单元格）同样经注入的 Docs 读取器，不把 OCR/PDF 搬进共享包
  （AGENTS.md 明令）。
* **代价**：Docs 代码在 portal 进程里被导入（accounts 角色已如此导入 `DocumentIngest`）。

## D4 功能开关只隔离未验收的应用

* **决定**：`SYNWEVO_SUITE_APPS`（逗号分隔的应用 ID）决定哪些新应用在注册表里登记为可用；未列出的应用
  路由返回 404、不出现在应用目录与 Agent 工具目录。本地与隔离验证实例列全部；生产只列已验收的。
* **依据**：主提示词 §12 P6。

## D5 个人 Agent 的模型：服务端提供方适配器，未配置就明确显示未配置

* **现场**：仓库与本机都没有任何模型提供方配置（首轮调查：无 SDK、无密钥变量；`ANTHROPIC_BASE_URL` 属于
  Claude Code 宿主，不是产品配置，不得借用）。`docs/platform/public-pages.md:48` 公开声明“没有 AI 处理用户内容”。
* **决定**：实现真实的提供方适配器（Anthropic Messages API，经锁定的官方 Python SDK，模型名来自配置）；平台级密钥由运维以
  环境变量提供，个人自备密钥以 `synwevo_core.sealed` 加密存储；工作空间“允许的 AI 数据处理提供方”策略默认
  **关闭**——未开启的空间，其业务内容不会进入模型上下文（工具仍按权限执行，但结果不回送模型，只返回给用户
  并说明原因）。上线前同步修改公开的数据与隐私页面。
* **阻塞**：真实模型链路的验收需要持有人提供一个提供方密钥（见 STATE.md「阻塞」）。测试用明确标注的替身
  驱动工具循环，不作为生产回退，也不算真实模型验收。


## 2026-10-02 · 保持串行完整运行时门槛，按实际容量调整时间预算

CI36997527305执行2925/4214项用了39m37后因SDK来源断言停止；当前4257项按同速含构建预计约63min。verify作业上限由45改75分钟，保留2CPU/3GiB、断网、清理、全部测试/断言/skip及verify成功后才deploy的依赖。75是运营时间预算，不改变测试中的性能/正确性要求。GC分批实测净省3.4%不足以支持改清理；分组发现testdata生成顺序及不稳定nodeID，暂不引入分组或新增Actions依赖。若完整新CI仍出现内存或业务故障，继续定位根因，不无限加时。

## 2026-10-03 · CI容量按根因处理，不再加时

6052c13 verify用74m36/75。测量显示每测固定成本（全量gc、每测新库建表、生产Argon2）约占75%。以测试基础设施修复（gc.freeze、迁移模板库、测试Argon2参数，均保留测试与断言，相关参数/迁移/计时测试显式保留生产路径）代替继续加时；6533c2a verify降至24min。75分钟预算不变。撤回条件：若模板库或测试参数掩盖真实缺陷（如本轮暴露的GCRA问题），优先修产品并补显式标记测试。

## 2026-10-03 · 跨空间内容转移：一份共享数据流契约（C1–C4）

* **现场**：Agent 读过 A 的内容后向任何其他空间写入一律 `source_transfer_unavailable`；流程跨空间引用一律
  `flow_transfer_policy_required`；`forms.duplicate` 跨空间只核对目标写权限，不核对来源导出与目标受众。三处口径不一。
* **决定**：`synwevo_core.data_flow` 是唯一判定“S 的内容成为 T 所有之物的一部分”的地方，Agent、Forms 复制和流程
  共用。依次核对：本人仍是 S 的有效成员且有导出权（owner/admin/editor 或 `can_export`，与 Docs “读取≠导出”同义）；
  S 的外发政策（`none` 不外发 / `audience` 目标空间每位成员都已是 S 成员 / `organization` 同一组织 / `any`），
  个人空间默认 `any`（只有本人），组织内空间默认 `organization`，其余共享空间默认 `audience`（没有人因转移获得原本没有的阅读面）；S 所属组织的上限
  （`organization` 默认，组织内容不出组织 / `any`），组织上限只能收紧不能放宽空间政策。空间政策由该空间
  owner/admin 设置；组织上限仅组织所有者设置、写组织审计。
* **绑定**：Agent 的确认卡展示来源空间、目标与受众；确认指纹包含每个来源的政策修订号、组织上限和受众，任一变化
  确认失效；执行前再判定一次。流程在每次解析引用时以流程所有人当前权限重新判定。
* **不做**：不按字段追踪模型输出中哪些字来自哪个空间（做不到可靠），因此分支里传给模型的任何来源都视为可能
  转移；读操作的参数不视为转移（已知局限，记录在 docs/platform/data-flow.md）。转移后的独立产物按目标空间
  权限管理，保留来源空间 ID 作出处，不保留来源正文依赖。

## 2026-10-10 显式业务上下文沿用来源与报价契约

首批资源为Forms定义和Tables字段视图，经现有list/get读取，客户端只传引用及指纹；用户消息api_json保存有界快照、sources_json保存来源，不新增数据库/迁移。引用进入quote/start/持续授权同一摘要，空引用兼容旧摘要。合法复制目标的读取权限与原始来源导出约束分离，手动选择与工具读取共用carried规则；不把复制来源误当持续读取依赖。个人文件另做user归属，不借任意workspace容纳私人附件。

## 2026-10-10 个人文件采用有界内联存储与完整解析

每项8MiB、每人50MiB的个人临时文件在Store拥有的agent_files表存inline BLOB，删除/过期/账户终结与清除字节同事务，避免依赖尚未部署的共享blob垃圾回收。只存user ID；来源依赖file_id与不可变解析指纹，workspace_id为null。旧Agent无法证明此来源时保守隐藏/停止。旧账户代码通过终结删除触发器清除新增内容，pending_delete仍保留恢复。

Docs另提供严格extract_context契约，现有成员导入/Extract截取语义不变。首批UTF-8文本/CSV/TSV与可完整诊断的纯文字PDF；不能完整读取或超限时拒绝全件，明确不支持OCR/Office，而非拿成员导入的截断样本充当Agent完整附件。读取在共用的有界可终止进程内执行；文件仍受现有扫描/持续隔离，扫描未配置时不声称安全通过。

## D6 本人通知不开放机器 API：个人范围的数据只经本人会话读取（2026-10-12）

* **决定**：`/api/auth/notifications*`（列表、未读数、标记已读、偏好）保持只对本人会话开放，不为机器凭据
  （`/api/suite/v1` 的服务账号、CLI/SDK 令牌、委托授权的 OAuth 客户端）增加读取或标记本人通知的入口；
  `docs/platform/api-coverage.md` 第 82 行的"本人通知的机器读取未开放"改为**按设计不做**，不再列为缺口。
  需要让外部系统知道"发生了什么"的集成，用已有的空间级事件（`member_events`、`template_events`、Forms/流程事件，
  经 `synwevo_core.capabilities` 的事件授权与 Docs webhooks）——它们只带标识符、按空间与应用授权、可审计。
* **依据**：`synwevo_core.capabilities.AppManifest` 规定只有工作空间范围的应用才可有机器 API
  （`machine=True` 且 `scope != "workspace"` 直接抛错），通知属个人范围：它跨越本人所有组织与空间，内容卡片引用的
  对象各有各的空间授权。一道"读本人全部通知"的机器入口会成为跨空间的第二道门——持有一个空间服务凭据的调用方
  能看到本人在其他空间收到的通知标题与对象，绕过 `authz.py` 与空间批准；要做到不越界，就得为每条通知再算一次
  来源空间与凭据范围的交集，并对卡片内容做二次脱敏，复杂度与风险都不小于收益。
* **代价**：个人自动化（"把我的通知转到别处"）没有官方 API；浏览器推送（Web Push，已有）与邮件通知是本人在
  设备上接收通知的两条路径。
* **撤回条件**：出现明确的产品需求且满足三点时再开：按通知来源空间与凭据的空间/应用范围求交集过滤；卡片只给
  标识符不给标题正文；每次读取写审计并受本人显式授权（OAuth 同意页列出"读取你的通知"）。届时作为新的
  个人授权范围设计（委托令牌而非服务账号），并补 `api-coverage` 与权限矩阵测试。

## D7 应用内写操作的审计（G26）：一个写入口、按身份描述符归因、写入与审计同事务（2026-10-12）

* **现场**（只读清单 `scratchpad a13/g26-plan.json`，4 路并行盘点 + 合成）：Synwevo Docs 约 93 条状态变更路由中约 80 条不写
  `audit_events`（项目、来源/OCR、模板/字段/版本、映射、任务创建/取消/重试、上传、签名下载链接、回归用例、评论，以及全部 8 条 webhook
  路由）；13 处内联审计用三种不同的操作者约定，且多数在服务返回之后写（与写入不同事务）。Suite 的注册操作经 `Capabilities._finish → _audit`
  每次恰好一行、与事务性操作同事务，但 `actor_kind` 恒为 user、`ip` 为空、无拒绝/失败/异步完成行；匿名公开填表、`acting()` 包裹的字节
  搬运路由、自动化触发的流程启动、后台 worker 状态迁移、Suite webhook ping/redeliver/系统停用绕过中央路径。投递尝试只存在于
  `webhook_deliveries` / `suite_webhook_deliveries` 行上，无逐次签名元数据。
* **决定**：
  1. 唯一写入口是 `synwevo_core.auth.audit_event`；它新增 `actor: dict | None`（`authority.describe*` 产出的描述符），统一映射：
     `user`→(`user`, user_id)；`delegated`→(`user`, user_id) + `meta.delegation={kind: oauth_client, client_id, grant}`；
     `service_account`→(`machine`, `service:<client_id>`)；`api_key`→(`machine`, `api:<key_id>`)；`operator`/None→(`system`, 调用方给的标签，
     命令行一律 `cli:<os user>`)。`request is None` 时 `request_id` 回落到 `synwevo_core.context.request_id()`。`http.Platform.audit`
     与 `Capabilities._audit` 都委托给它；当前主体经 `context.principal()` 提供给服务层，由网关在设置 `_actor` 处一并设置。
  2. 写入与审计同事务：审计写在服务方法内部、与业务语句同一个 `Store.tx()`；今天在路由层事后写的行一律搬进服务并删除路由层的那一行，
     一次写入在浏览器与 v1 两个表面只产生一行；幂等重放（`Idempotency.run`、`JobService._idem_find` 命中）不再写第二行。
  3. 只记标识符：`before/after/changed` 经 `audit_change.change` 只覆盖标识符键（template_id、template_version、archived_at、step、
     head/version 号、mapping_rev、订阅 status/events/host），名称、规则文本、映射标签、评论正文、URL 路径与查询、令牌与密钥永不入 meta。
  4. 异步操作两行：请求时一行（受理）与完成时一行（同一 `run_id`，`meta.phase=completion`）；现有按行数断言的测试随之调整。
  5. 拒绝与失败：被回滚的写入在回滚后以独立事务写 `result=error` 一行；机器调用方（`via != ui`）被网关/授权拒绝的写入写 `result=denied`，
     按（操作者, 动作）每分钟至多一行，防止扫描凭据灌表（G23 同法）；浏览器会话的拒绝沿用现状不记。
  6. 匿名公开填表只审计提交、上传、放弃三种，`actor_kind=anonymous`，`actor_id` 为填写者密钥哈希前缀；草稿自动保存不记。
     实时协同只在产生版本时记（collab/save、save_builtin → new_version），不按每次草稿写入记。评论四种操作都记。
  7. 文件系统对象（回归用例目录、上传分片、blob）无法与行回滚：顺序固定为先写审计行、再改文件系统；其余写入由同事务机制统一阻断
     （审计写不下去就写不进业务），不区分"高风险/普通"。
  8. 投递尝试留在投递表：为 `webhook_deliveries` / `suite_webhook_deliveries` 增加可空的签名密钥 ID 与时间戳列（不存签名本身），
     ping/redeliver/系统停用的审计行引用投递 ID。
  9. 账户服务不得导入应用（边界规则）：`docs.*` / `suite.*` 动作的三语言标签作为数据放在 `services/identity` 的动作目录里，由各应用
     套件的源码扫描测试保证每个被审计的动作字面量都在目录中；目录到位前审计页按原样显示代码。
* **代价**：Docs 服务层约 40 处方法各加一行审计并补事务包裹，分 10 个工作包、每包一次提交；Suite 若干按行数断言的测试要改；
  投递表加两列（唯一迁移点 `store.py`）。
* **撤回条件**：若 `audit_events` 单表写入成为性能瓶颈（评论、上传分片状态等高频写），再把应用动作分表——此前先按动作加索引与保留期，
  不回到"路由层事后写"的模式。
* **待核实后再动**：`ProjectCopies.decide` 被 Docs 自批与账户服务审批路由两边调用，先确认后者是否已审计，再把复制审计搬进共享类（WP3）。

## D8 平台受控所有权恢复的边界（A37，2026-10-12）

* **现场**：D11 已阻止停用/删除最后一名可用拥有者，但孤儿仍会出现（D11 之前的数据、脱离组织的拥有者、运维直接改表），此前唯一出路是运维改库；认领流程只覆盖历史 `pending_claim` 空间。
* **决定**：
  1. 平台只能把**现有成员**指定为拥有者：不创建账户、成员关系或资格；拒绝管理员自己与任何持有平台授权的账户（与认领流程同一规则——平台角色永远不是通往文档的路径）。
  2. 孤儿判定与 D11 同一规则：拥有者行的账户活跃且未删除，附属工作空间的拥有者还须仍在组织内；待认领历史空间走认领流程，已删除/过恢复期的组织及其空间、只剩本人的个人空间不在范围内；冻结与可恢复的组织仍需要管理者。
  3. 接任者必须持有产品认可的第二因素：与 A11 管理员第二因素策略同一判定 `PlatformAccess.step_up(uid)["second_factor"]`（已确认 TOTP 或绑定当前 RP 的可验证用户的安全密钥）。对抗性审查指出最初采用的 `has_second_factor`（仅 TOTP）既比产品规则窄、又与所有者步进（密码重输或安全密钥仪式）的前提不符，已改为同一判定；清单用同一规则的 SQL 片段一次查出候选资格（每个孤儿一条查询，候选最多 100 位、符合条件者优先，另给 `candidates_total`）。
  4. 权限按对象对齐：空间 `platform.workspaces.manage`，组织 `platform.orgs.manage`（覆盖表草案统一写 orgs.manage；两者由同样的两个角色持有，差别只在语义归属）；读取清单需同时持有两项查看权限。
  5. 单事务：`owner_present` 在写事务内复核；组织路径同事务写组织自己的 `organization.owner.recover` 与平台的 `admin.organization.recover_owner`（两类读者），任一审计失败整体回滚。
* **不做**：不自动通知接任者（通知属于后续包）；不提供“先把任意账户加入空间再任命”的路径；不以此替代组织自助转让。

## D9 控制台的组织生命周期是平台作用域操作（A35，2026-10-12）

* **现场**：`POST /api/admin/organizations/{oid}/{action}` 虽要求 `platform.orgs.manage` + 严格重认证，却委托给要求组织拥有者身份的 `Organizations.transition`；非成员的平台管理员得到 404/403，控制台只对恰好是拥有者的管理员显示按钮。平台能逐个冻结空间，却不能冻结组织。
* **决定**：
  1. 控制台路径一律是平台作用域：持 `platform.orgs.manage` 的管理员无论是否为成员都走同一条路（预览 → 理由 → 严格重认证 → 幂等确认）；拥有者自助仍走租户路由 `organization_routes.py`，行为不变。
  2. 一个状态机、两个入口：`transition`（拥有者）与 `platform_transition`（平台）共用私有体，差别只在入口检查、组织审计的 `by: platform` 标注和返回视图；安全日志与撤销（会话、令牌、授权码、邀请、API 批准）完全相同。
  3. 高风险确认复用 A14 协议（`SignedPreview` + `admin_operations`）：预览令牌绑定操作者、组织、动作、来源、版本摘要（状态、删除/恢复时间、前状态、版本、成员数、空间数）、过期与随机数；确认时在写事务内重读并复核；同键同体重放返回原结果且不再写。
  4. 影响预览只给计数（成员、空间、会话、令牌、待决邀请、API 批准、运行中的 Docs 任务），按实际撤销谓词统计，跨应用表以存在性守卫的 SQL 读取、不导入应用；不展示任何业务内容。
  5. 平台操作不新增任何成员关系、资格或令牌；两条审计面向两类读者（组织自己的治理日志 + 平台审计），同事务。
* **不做**：永久清除（A36 另议）；不自动通知组织成员（通知属后续包）；不改变租户自助路径。
* **授权依据**：admin-coverage §4.2 第 5 项曾把“平台能否冻结或删除非本人拥有的组织、孤儿由谁接手”列为待所有者决定；统一执行要求 §12.2（最高管理员治理资源生命周期与系统状态）、§15.3（管理组织/空间状态、归档/恢复、所有权与受控交接，保护最后拥有者）已作出要求，D8/D9 按此实现；该项其余部分（平台级邀请、组织创建策略、永久清除）仍待决定。

## D10 平台批量账户操作是一组单账户操作（A18，2026-10-13）

* **决定**：
  1. 批量停用 / 批量撤销会话不是新的、更宽的权力：每个账户在自己的写事务里执行与单账户路径相同的检查与写入（同一共享写体），拒绝的账户不阻止其他账户，响应逐项列出。
  2. 一次预览、一次确认：签名计划绑定操作者、操作、来源、过期与预览时**可处理**账户的有序 [账户, 版本摘要]；预览时不可处理的账户永不处理；确认时逐个重算摘要，变化者单独报告 `admin_preview_stale`。
  3. 幂等分两层：批次键重放返回已存结果；逐项回执 `<批次键>:<账户>` 与该账户的写入同事务，超时或崩溃后同键重试只续做未完成的账户，不重复写安全日志（在预览有效期内）。
     批次键在首个事务即以“运行中”行占用：同请求续做同一批次，不同请求立即冲突且不执行任何账户；预览过期后的续做把剩余账户报过期并关闭批次。
  2a. 预览按执行顺序模拟：停用预览在写事务的保存点内把前序可处理账户视为已停用，使后续账户的唯一拥有者判断与执行时一致；签名前回滚，不留任何写入。
  4. 只作用于显式选中的账户 ID（最多 100），不提供“按筛选条件全部”；同步执行，确认前关闭即取消，失败子集以新预览重试。
  5. 权限按操作：停用 `platform.users.manage`，撤销会话 `platform.users.support`；均需严格重认证。
