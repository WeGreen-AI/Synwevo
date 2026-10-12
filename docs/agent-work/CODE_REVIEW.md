# 统一代码审查记录

2026-09-30：采用 MASTER_PROMPT 新统一范围；当前集成基线 feat/business-apps@8e8fcec，主检出API@2dd03c7，Admin隔离分支@6cef796（仍活动）。旧审查 [codebase-review](../engineering/codebase-review.md)、[review-findings](../engineering/review-findings.md) 是历史材料；本文记录本轮实读及复核，不声称逐行审查全仓。

| ID | 位置/调用链与证据 | 影响/严重度 | 来源 | 处置/验证 | 状态 |
|---|---|---|---|---|---|
| CR-001 | Forms下载→Store.get_blob；Filling.file_bytes同；Extract下载/worker/result缺少quarantine.standing检查。Store.get_blob只读路径，无隔离判定 | 高：后续恶意判定后，备份恢复的字节可旁路；衍生结果还可能继续流转 | 本次新应用初稿 | 复用全平台隔离事实；统一Suite读取检查，并对下载、重传、排队与衍生结果故障注入测试 | 已验证：最终285项Suite/桥接/库存回归通过，含DataPrep直接上传/衍生结果隔离 |
| CR-002 | agent/provider.py resolve_credential仅Anthropic；engine.py provider.step单入口；SDK max_retries=2；没有payer/reservation/attempt持久事实 | 高：不能直接启用平台收费，否则没有可靠预算与重复成本归因 | 原有Agent未发布实现 | 保留兼容；增加统一付费网关与账本；DeepSeek适配独立实现但未接通之前禁止启用收费 | 已审查，待实现 |
| CR-003 | flow_runs.input_digest为input规范化指纹；blob inventory守卫实际失败 | 中：引用库存定义遗漏，阻断门槛 | WP7增量 | 分类为LOOKALIKES并保留原因；原守卫复验 | 已验证：blob引用库存3项通过 |
| CR-004 | Docs解析重试/关闭竞态；origin/main同类修复合并，worker注册移到关闭状态初始化后 | 中：关闭后向执行器提交 | 旧Docs路径 | scan_retry与parse_shutdown专项；合并后210项通过 | 已验证8e8fcec |
| CR-005 | 8e8fcec Actions36693142151无verify步骤，billing annotation，deploy skipped | 发布阻塞，非代码通过 | 外部 | 保留门禁，不重复手工发布 | 受阻 |
| CR-006 | Admin权限/配置链在隔离分支，API已提交主检出2dd03c7但未合入business-apps | 集成/回归风险 | 并行专项 | 已读取能力表/提交与任务状态；由现有Admin任务继续自身文件，统一协调者集成共享迁移 | 待集成复核，不沿用其自述为本轮验收 |

| CR-007 | DataPrep取消→check_run_sources，来源撤权或隔离后连停止排队工作也被拒 | 中：恢复/取消行为不完整 | Extract导入增量 | 取消只要求当前目标DataPrep写权限，返回无来源内容的最小收据；历史重放同样不泄露，新增拒权与撤销测试 | 已验证：20项来源/取消专项，最终285项集成通过 |

本批已完整走读 Forms附件上传→提交→下载、Extract上传→任务→worker→来源→复核→结果、server适配器工厂→Docs运行/消息投递；Agent模型单入口初步走读，钱包/支付搜索无既有实现。Admin/API细节引用下列原有能力索引，未实读调用链保持待核实。

详细领域索引：[Admin](../platform/admin-coverage.md)、[API](../platform/api-coverage.md)。统一优先级、验收及发布状态以 BACKLOG 为准。每批diff和相关旧路径继续记录于此；不会复制秘密、用户正文或支付负载。

- WP7 UI实读五个新文件及Agent路由，操作来源为真实注册表；浏览器发现截止日期误用时长标签、停用数字0渲染，均修复并实测。仍待：通用结果的业务呈现、草稿恢复、完整跨空间数据转移政策、实际reduced-motion环境。

- CR-006集成：e9b9f2c/API、43b221c/Admin正常合并。实读平台配置版本服务、管理签名预览/发布、迁移入口及身份授予差异；发现旧Suite管理员夹具不符合新授权模型，修正后36项复验通过。完整Admin面仍不声称逐行审查。
- CR-002推进：实读DeepSeek全部协议/终端usage与账本models/ledger/DDL；接入持久gateway及Agent工具循环。100项新联合检查通过；正式配置/价表发布/钱包同意/支付/限流尚未连接，不启用收费。原始usage仅保留计量字段，provider模型字符串不作为有效路由证明。

| CR-008 | gateway正常返回即completed=True，engine随后拒答/截断/跨步骤重复tool ID | 高：失败输出按成功收费 | 新计费网关41069bd | 独立审查真实Store/Agent+协议夹具复现；临时验证树提前纯验证，可信成本保留、失败零销售；新故障专项通过 | 0371c20已验证并整合 |
| CR-009 | gateway未知检查与Ledger.dispatch分离，并发两逻辑步骤越过检查 | 中：未知用量未阻止新发送 | 新计费网关41069bd | dispatch同事务检查run内未决attempt；并发屏障测试仅一次发送、保留297fixture预留 | 0371c20已验证并整合 |
| CR-010 | _observe/settlement写入异常后Agent默认usage known0 | 中：账务展示误导 | 新计费网关41069bd | 付费调用前持久pending；可靠完成且无旧未知才恢复known；2种数据库故障公开input/output null | 0371c20已验证并整合 |

- f6fb865钱包读取审查：拒绝原逐页全历史扫描，改为账本同一事务的索引汇总；当前用户/环境绑定，金额和游标字符串，返回前再次检查会话。44项HTTP/账户与100项关联回归通过；退款/争议扩展前需更新消费口径，页面不冒充完整计费交付。

| CR-011 | ops.result_guard将agent.turn.quote误当已有turn，幂等回放没有turn_id | 中：报价成功后同键重放失败 | 本批报价增量 | 排除quote分支，继续用conversation当前授权；真实共享操作回放与HTTP隐私测试 | 235项关联测试已验证 |
| CR-012 | BudgetDialog编辑表单在报价完成后移除，Enter路径焦点回到页面 | 中：键盘/读屏丢失上下文 | 本批预算UI | 将焦点移到总预算摘要；390px真实Enter/Tab/Escape复验，关闭恢复触发按钮 | 已验证 |

- 完整复读quotes→engine.start事务→MeteredTurnProvider→Ledger授权与recovery本地结算/终态投影链，单次授权不授予工具/外发权限；latest provider resolver保持显式装配。新binding_digest按非blob指纹登记，原库存检查3项通过。
- 未完范围明确保留：默认生产未装配付费路线，当前每次单次报价尚不能替代§28.11低风险持续预算体验；恢复内部诊断尚缺Admin处理面，过期未使用报价尚缺保留清理策略。未以局部测试冒充完整收费交付。

| CR-013 | CostAdmission初稿以usage缺失条件联查全部历史，Python读数上限未限制SQLite历史扫描 | 中：账务增长后发送准入变慢 | 本次成本准入审查 | Store原子维护pending投影，当前区间+历史pending索引UNION，先LIMIT再联查；10,000旧已结算记录的真实SQL计划验证无底表全扫描 | 已验证：最终289项联合通过 |
| CR-014 | 成本准入拒绝未发请求，Agent默认pending会永久把其显示成未知usage | 中：费用状态误导、已完成部分不易核对 | 本次真实Agent故障路径 | 内部not_dispatched分类；无dispatch时间戳且已释放attempt恢复为已知零；真实已发送未知仍pending。混合已完成工具/后续拒绝保留已结算用量 | 60项故障/恢复及10项成本网关专项通过 |

- 成本准入服务、Ledger dispatch事务、Store迁移/触发器、Agent错误传播/终态恢复已走读。Popper的两个文件已审阅回收，未触碰主检出图标任务。当前是发送前预算门槛，不含完整提供方治理、队列/速率、周期付款预算或Admin成本界面；正式付费工厂仍未启用。

## CR-015 · 成本策略UI预览与真实到期字段不一致（已修复）

初稿用Number.isSafeInteger校验expires_at，但真实SignedPreview使用time.time()+300浮点秒，导致所有正常预览被拒绝。改为有限number及有效时间范围，保持服务端期限原值；Accounts类型/构建与前端检查通过，浏览器仍被客户端拦截，不能声称已视觉验收。

## CR-016 · OpenAPI导出依赖Python状态短语（已修复，CI待新提交）

7190408 CI容器首个失败按收集顺序定位为test_openapi_file_is_in_sync。相同工作树只读挂入已有Python3.12隔离镜像，复现413 `Request Entity Too Large`与3.14 `Content Too Large`差异，随后暴露416 `Requested Range Not Satisfiable`与`Range Not Satisfiable`。上传接口显式声明现有契约用语并保持键序，未改已提交OpenAPI或断言。最终3.12/3.14两项精确同步/上传契约测试分别通过；最初只固定413、以及416键序不同的中间失败如实保留在过程。CI验证命令新增首败退出、20项耗时和skip原因，成功仍跑全部配置套件。45分钟容量问题单独调查，尚未宣布修复。

## CR-017 · 协作观察者保留整个应用及测试客户端（已本地验证，CI待新提交）

`Room.__init__`长期Doc.observe回调强引用Room，pycrdt原生回调使正常GC无法释放Room/Hub/API/Store；SheetRoom/只读presence也继承了无用观察者。新隔离回归以真实Docs工厂、两次编辑/关闭/重开、SQLite关闭写入拒绝验证四种场景，每种六个独立应用。旧产品4项全失败，真实草稿断言先通过，仅对象图保留失败。当前改为在两个同步Doc写入入口capture_changes，finally退订并完成不改内容的空事务，使原生释放生效；无需常驻回调/工厂全局清理。Peer测试夹具同样限定观察范围，原帧与业务断言不变。初次尝试在房间关闭时退订暴露Subscription线程绑定错误，该方案已撤回；没有屏蔽warning/异常。最终210项协作+33项内存/工厂/上传关联通过。完整CI超时是否解决待新SHA实测，不声称已证明唯一原因。

## CR-018 · 损坏草稿重建后无法广播后续修改（已本地验证）

Room.load损坏路径替换Doc，但原常驻观察者仍留在旧Doc，新Doc修改不进入pending，其他编辑者和dirty通知收不到。复用CR-017操作范围观察解决，原损坏恢复用例增加第二名真实协议参与者，核对即时文本、dirty广播和最终数据库草稿。210项回归包含该场景与撤权、关机、关闭竞争、重开、版本折叠；未改变自动保存/版本一致性规则。

## CR-019–022 · 提供方治理与实际派发边界（2026-10-02，当前增量）

| ID | 位置/实证 | 影响 | 修复与验证状态 |
|---|---|---|---|
| CR-019 | engine._check_context仅比较kind/model/provider，队列/重启未固定具体密钥 | 高：轮换或撤销后继续用旧凭据 | 持久credential_binding，worker恢复及每次实际派发重检；旧NULL运行拒绝、不回填；24项新治理测试通过，联合197项通过 |
| CR-020 | 跨空间复制调用以源空间过滤模型结果，历史却登记目标空间 | 高：目标禁止处理时仍可能外发目标元数据，后续政策变更检查归属不一致；未证明正文泄露 | 统一结果所属空间用于外发/历史/来源；合法独立复制产物按目标治理，其他源读取保留源约束；真实Forms/Capabilities用例验证 |
| CR-021 | MeteredTurnProvider缺resolver时借用Anthropic解析器，Credential.label写死提供方 | 高：配置混用，提供方/付款范围不明确 | DeepSeek无解析器即不可用，传输与计量入口严格提供方检查；显式工厂接通治理配置、报价、Ledger授权、CostAdmission与当前Agent权限 |
| CR-022 | dcca816 CI管理权限矩阵遗漏价格7/成本4端点 | 中：统一逐角色门槛未覆盖新增路由，CI实际失败 | 补齐15端点（包含本批模型治理4个）；真实有效请求、PATCH、合成价格 fixture，各平台角色/普通用户/组织管理者均覆盖；本机28项与Python3.12关联150项通过 |

当前仍在补工作空间明确提供方范围，旧组织platform/any不得隐式扩大到DeepSeek。工厂11项测试使用真实Store/Agent/Capabilities/计费服务，模型SSE及资金为明确隔离夹具；不表示真实推理/收款或上线。界面同源再次被ERR_BLOCKED_BY_CLIENT阻断，未绕过。


## CR-023–025 · 付款方周期限制（2026-10-02，bff35a8后增量）

| ID | 证据与风险 | 修复及验证 |
|---|---|---|
| CR-023 | reserve固定created_at而周期检查重新取时；事务恰逢UTC日/周/月边界会将新周期准入记入旧周期 | 检查与call INSERT使用同一固定时间；三个边界独立回归通过 |
| CR-024 | 新预算读操作初稿缺unattended=False，可能向工作流开放个人财务配置 | get/set均need personal、agent never、unattended false；模型/flow/automation拒绝与当前本人HTTP鉴权通过 |
| CR-025 | 派发预算拒绝被上游统一成provider_dispatch_blocked，用户无法理解额度原因 | 只保留payer_*明确拒绝；原未知用量/fence传输语义不变。真实工厂验证拒绝不发网络并释放hold |

本次审查覆盖PayerBudgets、Store DDL、Ledger reserve/dispatch、实际GovernedDeepSeek工厂、Capabilities事务/审计/事件、Agent操作与设置状态机。独立恢复测试含旧账务升级、不同时区边界以UTC固定、多单位、真实10000/10001候选边界及事件/audit INSERT后故障；没有关闭约束或把异常当零。模型/支付均隔离夹具，未宣称真实收费验收。


- CR-026：预算前端初稿将spent/held合计限制为单笔int64，合法旧账务多个结算可能超出，造成整页不可读。输入/上限继续int64，合计按10000候选最大金额边界验证，BigInt精确显示；两笔最大金额合计前端回归通过。不是放宽付款上限。


## CR-027 · CI验证镜像SDK来源不一致（已本地修复）

bff35a8真实CI在test_dataprep_extract.py来源断言失败：SDK被Dockerfile.verify常规安装到site-packages，其他模块是editable当前检出。验证镜像统一SDK来源安装；保留原断言。实际修改后Dockerfile诊断构建及17项相关/27项SDK通过。宿主机SDK仍常规安装，两个安装用例目的不同。完整新SHA门槛待推送。

## 2026-10-03 · 本会话（协调者 df1f0b15）审查与处置

| ID | 位置/证据 | 影响/严重度 | 来源 | 处置/验证 | 状态 |
|---|---|---|---|---|---|
| CR-028 | agent/ops.py model_consent 捕获 BillingError 但模块未导入，同意的全部拒绝变为 500 operation_failed | 高：所有拒绝原因丢失，前端无法恢复 | 在途同意初稿 | 模块级导入；not_found 映射 404；新语义测试 23 项覆盖每类拒绝码 | 已验证 21f2cd5 |
| CR-029 | 同意守卫用拒绝列表（agent/flow/automation），via=system/api 可创建/使用他人钱包的持续同意 | 高：机器/系统路径可替本人花费 | 在途同意初稿 | 改为仅 ui；4 类调用方反向测试 | 已验证 21f2cd5 |
| CR-030 | BudgetDialog 在保存同意后因页面刷新同意列表而撤回同意步骤，并恢复已退役报价的单次确认按钮 | 中：用户可能点击已失效报价，流程迷惑 | 在途同意 UI | 对话框打开时固定同意选项；真实浏览器复验 | 已验证 21f2cd5 |
| CR-031 | api_quotas GCRA 以时间保存债务：清除 3/min 覆盖后旧债务按 1200/min 记满一分钟；收紧限额又给新突发 | 中：管理变更不按“下一请求生效” | 原有 API 平台 a3b52cd（由更快测试暴露） | 记录计费间隔，按请求数换算，收紧最多满桶且拒绝时也记录；单元+HTTP 回归 | 已验证 6533c2a，CI 37105534440 通过 |
| CR-032 | 每测固定成本：全量 gc.collect、新库 ~70 次 ALTER、生产 Argon2，CI verify 74m36/75 | 高：发布门槛即将超时 | 原有测试基础设施 | gc.freeze、迁移模板库、测试 Argon2；参数/迁移/计时测试显式保留生产路径 | 已验证 00c4f54，CI verify 24min |
| CR-033 | 管理端成本策略表单未知 traffic 字段会在编辑时被丢弃；旧版本序列化新增 "traffic": null | 高：管理员一次普通编辑即静默取消限流 | 本批 traffic 初稿 | 表单显式“启用/不设置”；spec 序列化省略未设置 traffic；测试覆盖往返与旧版本形状 | 已验证 27ab127（本地） |
| CR-034 | flows/triggers.py 只按工作空间应用读权投递事件；Tables 低于 read_role 的表、失去来源的 DataPrep 行仍触发并带 object_id | 中：对象存在性泄露、按不可读对象运行 | 原有 WP8 | 并行会话 3f60059/aa81da3：同一 EventFeed.visible 判定，隐藏即不运行、不留痕、不回补 | 已合并 ed1b0a9，本地全量复验中 |
| CR-035 | flows.trigger.get/list 返回原始全局 flow_triggers.cursor；拒绝收据在用户失去应用后仍存事件 id | 低：泄露全局事件计数/事件存在 | 原有 WP8 | 已指派并行会话分别修复并补测试 | 待修复 |
| CR-036 | WP9 两轮独立安全审查（机器入口、延后工作、文件路由、事件）：standing 对畸形 principal 放行、Tables 票据按人不按客户端、内层拒绝泄露来源 scope、started 事件早于授权检查等 | 中/低 | 并行会话 WP9 | 全部按审查修复并有反向测试 | 已合并 ed1b0a9 |
| CR-037 | `forms.form.duplicate` 跨空间只核对目标写权限：来源只读成员可把表单定义复制到任意可写空间；Agent 写入则一律拒绝（`source_transfer_unavailable`）、流程一律拒绝（`flow_transfer_policy_required`）——三处口径不一，合法跨空间转移无路可走 | 中：越过“读取≠导出”；C1/C2 不可达 | 原有 | 新共享契约 `synwevo_core.data_flow`，三处共用；Agent 在提议/确认/执行三处判定并绑定政策修订号；四项变异测试均失败 | 已修复 a407c2c |
| CR-038 | 消息桥 `audience_fingerprint` 为一个哈希函数导入 capabilities，使账户服务可达能力注册表及其拒绝文案（account refusals 测试捕获） | 低：账户进程加载业务注册表 | 本会话 0e95fcc 引入 | 就地计算同一规范 SHA-256，摘要与 capabilities.fingerprint 一致已验证 | 已修复 67b08c4 |
| CR-039 | `services/identity/tests/test_admin_model_costs.py:483` 断言 `"601" not in r.text`，而错误响应体带随机十六进制请求 id；67b08c4 的 verify（Actions 37124810853）恰好得到 `1833f60141cff808`，1 failed / 1574 passed 后按 `-x` 停止，deploy 跳过 | 中：与产品无关的偶发 CI 失败（每次约 1%–2% 概率），阻断发布门槛 | 原有测试（27ab127 引入） | 比较去掉 `id` 后的响应体，仍断言提交值不回显；同类模式全仓 grep 仅此一处 | 已修复 e6018ff（本地 74 passed；CI 复验待新 SHA 结果） |
| CR-040 | 流程步骤可为无空间的个人操作：`agent.memory.add`/`agent.turn.start` 把空间内容写入个人记忆/会话，`agent.conversation.get` 把会话里的空间内容交给另一空间的写入；`data_flow.check(target=None)` 在 `audience`/`any` 下放行（`flows/engine.py::_operation`、`data_flow.py::check`） | 阻断：未提交 data_flow 改动引入的回归（旧代码对 target≠source 一律拒绝），绕过导出权、`none`、组织上限与 AI 处理政策 | 本轮 data_flow（未提交）| 流程拒绝一切 `need=personal` 步骤（`person_required`）；`check` 要求目标是本人所在的有效空间（`transfer_target_unavailable`，在读取任何政策事实之前判定，对不存在的空间答复相同，同时消除成员数探测）| 已修复 a407c2c：独立审查 13 项复现脚本全部转为失败；回归测试经变异验证；对抗复核进行中 |
| CR-041 | Agent 调用个人只读操作 `agent.flow.run` 取得流程步骤的业务快照，结果无来源空间标记：只读成员可把 B 的内容经此带入 A，且绕过 B 的 AI 处理政策（`agent/engine.py::_execute`、`flows/engine.py::run`）| 高 | 原有（WP8）| `via=agent` 时不返回步骤快照（页面仍显示）| 已修复 a407c2c，变异验证 |
| CR-042 | `audience` 只比较工作空间成员，但对外发送（消息、填写请求、公开链接）到达的不是目标空间成员（`data_flow.check`、`agent/engine.py::_transfer`、`suite_adapters.py` 发送）| 中 | 本轮 data_flow | `side_effect=external` 的写入在 `audience` 下拒绝（`transfer_audience_external`）；Agent 与流程两处传入；`organization`/`any` 由所有者决定 | 已修复 a407c2c，Agent 级与流程级测试各一，变异验证 |
| CR-043 | 同一流程运行内经第三空间中转（A→个人空间→C）逐跳检查，来源不随引用传递 | 中 | 本轮 data_flow | 按定义静态闭包计算步骤谱系（运行处、读取处及其输入引用的全部步骤），每个原始来源都对最终目标判定；跨独立操作的手工两步转移属导出权固有限度，已写入文档 | 已修复 a407c2c，变异验证 |
| CR-044 | 已共享的个人空间默认仍为 `any`；未知政策值按放行处理；空间政策写入无版本前置条件、组织上限的 `version` 可省略（旧页面可静默撤销他人的收紧）| 中/低 | 本轮 data_flow | 仅“只有本人”的个人空间默认 `any`；未知值按最严处理；`agent.transfer_policy.set` 必须带所见修订号并以条件写入落库（409 `transfer_policy_changed`），事件带 `before`；组织上限必须带 `version` | 已修复 a407c2c，变异验证（13 项变异全部被测试捕获）|
| CR-045 | Docs项目复制及组织模板发布未读取来源数据流政策；`none`仍可转出 | 中：来源转出政策存在旁路（原记录低，按实际复现提高） | 原有；复核发现新`as_tenant`会扩大目标机器委托，提交前修复 | 申请保存前、批准及复制事务内复核；组织新发布按全组织受众、来源内容资格与导出权检查；合法旧发布/副本独立；机器发布显式要求源空间当前委托；组织审批清单仅目标有效成员可读，分页前过滤 | 本地已验证：Python3.12相关93通过、前端496及类型/构建/守卫；浏览器三语言真实403、390px/reduced-motion、零副作用；f1dae29/Actions37403925697完整verify及deploy成功，07:07Z远程镜像/五服务/五域健康核对通过，证据`evidence/2026-10-06-transfer-policy` |
| CR-046 | Admin 运行页独立审查 AR-1–AR-10：`check_brand` 因未加引号的 `docweave` 键失败（CI 门槛）；`blocked/unknown/paused` 流程对“需要关注”、汇总与筛选不可见；调用方自定 `correlation_id` 与作者自定 FailStep code 作为自由文本进入页面；列表做全表工作；首次加载焦点被标题抢走；两处英文标签错误等 | 高/中/低 | 并行会话未提交实现（admin-governance）| 工作流 wf_653d06ea-6cf 逐项修复（d4755bc/a819e5d/f85cada）；独立核查（scratchpad/admin-runs-verify）另得 AR-1 中（调用方关联文本的摘要无密钥可猜测还原、按原文搜索可确认猜测、同文本关联两个人）、AR-2 中（无执行器持有的到期流程运行数日不被标出）及低 AR-3–AR-8 | AR-1/AR-2/AR-7/AR-8 已修复 3dc06d6（HMAC 按人摘要、原文不可搜、相关运行与摘要搜索按人分开、停滞标记进入待处理与汇总、三语事件标签），8 项变异全部被捕获；AR-5 不采纳（单列关联索引免排序，查询计划测试为证）；AR-3/4/6 记录不改（百万行级筛选与汇总耗时 0.1–0.6 s、移动视图分页）；已并入 feat/business-apps 078a2a1；浏览器验收待做 |
| CR-047 | 对 a407c2c 的三路对抗复核（wf_5138fc3a-478，18 项，17 项有通过的复现脚本，scratchpad/df-skeptics.json）：**高** F1 流程谱系只跟随 `$ref`，字面量重读中转空间即可洗掉来源；**高** F2/AGENT-DF-1 `audience` 放行对已向外部暴露记录的普通写入（填写页返回表单行的实时标题/说明，`forms.form.publish` 声明为 write）；**中** F3 事件触发串联两个流程跨运行带出、F4/AGENT-DF-2 分支与等待谓词读取步骤值不经转移判定（`none` 下亦然，Agent 还能从 `agent.flow.run` 的步骤路径读出）、AGENT-DF-3 会话内复制后来源丢失、DFP-1 默认组织政策/上限下对外发送可把组织内容带出组织；**低** F5–F7、AGENT-DF-4/5、DFP-2–6（含文档不实与 Automations 页文案） | 高：已推送到 feat/business-apps（草稿 PR，未合并 main、未部署）的 data_flow 仍可被流程/Agent 自动化绕过 | 本轮 data_flow（a407c2c）| 三个隔离 worktree 并行修复（wf_3435d8f7-ca4）：流程改为“运行级来源”保守规则（凡本次运行及其事件链读过的空间都算来源，任何有副作用的写入逐一判定）；填写页只提供已发布版本，新增“对外可见写入”标记，组织范围下对外写入拒绝；Agent 记住会话内写入带入的来源、对模型只返回流程运行状态、确认依据变化时重发卡片 | F1、F3–F7 已修复 43e7e3c（df-flows，协调者复核 diff 后快进合入）；F2/AGENT-DF-1 已修复 0f0124b（填写页与提交人通知只用所答版本；公开链接跟随最新版本时发布按对外发送判定 `delegation.transfer_from`）；DFP-1 已修复 af62448（组织范围内容不能经对外发送离开组织，规则 9 `transfer_organization_external`）；AGENT-DF-2 Agent 半边已修复 64cdf9d（流程运行进度按运行来源计入会话来源）；AGENT-DF-3 已修复 f7afc66（会话内写入带入的来源在转移判定中保留，处理政策仍随目标）；DFP-2（组织冻结/删除后任何应用操作含政策修改均拒绝）、AGENT-DF-5（会话范围约束 Agent 定义/启动的流程）、AGENT-DF-4（确认依据变化时重发卡片而非永久 409）、DFP-5 与 CR-048 DF-B1 前端部分（编辑器不再在客户端拒绝跨空间引用、不再提供个人操作为步骤、`transfer_*`/`person_required` 显示三语原因）本批修复；DFP-3/DFP-4 文档已改。每项均有变异验证（全部被测试捕获，DFP-1 一项为等价变异）。DFP-6 已修复 9048d40。第二轮对抗复核（scratchpad/df-round2）：无高；中 R2-F1（固定版本的公开链接可在提交时指名新版本读到其规则提示）、R2-F2（Agent 写入触发的流程链丢失 Agent 记录的来源）、R2-F3（会话兄弟分支丢失带入来源）、R2-F4（carried 按运行重复扫描整条链且调用方可自选链）；低 R2-F5–F8 —— 全部在本批修复，11 项变异全部被捕获，apps/suite+integration 1857 通过。剩余：CR-048 其余 |
| CR-048 | 对 a407c2c 的正确性审查（只读子代理，scratchpad/agents/dataflow-correctness/t/ 含复现测试；无阻断/高）：**中** DF-B1 自动化编辑器与服务端契约矛盾（仍可选个人操作为步骤、仍在客户端拒绝一切跨空间引用、`transfer_*`/`person_required` 无专门文案）；DF-B2 拒绝发生在派发之后（result_read 的检查在 `_complete_call`）时尝试次数已计入，政策修正后 `resume` 答 409 `flow_attempts_exhausted`；DF-B3 步骤等待异步回执期间遇到拒绝会丢失 `operation_wait` 标记，之后永远 `flow_reconciliation_required`（第三方改政策即可触发）；DF-B4 规格里不存在的 step_id 或无法再校验的已存规格使 `StopIteration`/`ValidationError` 逃出 `tick()`，`_wake_waits` 因此每次 tick 都失败、所有用户的运行都不再被认领（需损坏的行或跨版本回滚才可达）。**低** DF-B5（同 AGENT-DF-4）、DF-B6 拒绝未指明是哪个来源、DF-B7 八处保护没有会因其失效而失败的测试、DF-B8 文档/注释三处不实 | 中：流程可恢复性与引擎健壮性；不构成越权 | DF-B2/B3/B4 的 `_failure`/`_spec`/`_wake_waits` 为原有（WP7/WP8），其余为本轮 data_flow | DF-B1 余项、B2、B3、B4、B6、B7、B8 在第二轮三支修复合并后作为后续批次处理（引擎文件正由 df-flows 改写，避免并行冲突）；DF-B5 与 AGENT-DF-4 合并处理 | 待处理（排在 wf_3435d8f7-ca4 合并之后） |
| CR-049 | 签名 Webhook（suite 机器 API）管理侧与两路补审：管理侧修复（按授权与工作空间限制 ping、管理调用每空间一个检查位、文本可拒绝的先于 DNS、NAT64/空 userinfo、重新投递队列上限、SDK 缺 id 拒绝）82de9a6；补审（scratchpad/webhooks-review）WH-1 中（一个空间的慢但成功的接收端占满全部投递工作者）、WH-2 中（被放弃的 DNS 查询持续占用共享解析池，可由一个授权耗尽）、WH-3 低（IPv4 兼容/SIIT/site-local/组播 IPv6 被视为全局）、WH-4 低（SDK 验签接受空或过短密钥）、WH-5 低（管理订阅无需 read scope）、WH-6 低（收窄事件过滤不入日志，恢复后被放宽） | 中：跨空间公平与共享资源耗尽；无越权读写 | 本轮 | 每空间同时在途投递不超过工作者一半（新索引 suite_whdel_busy）；解析按来源计数直到查询线程结束、每来源至多 2；IPv6 仅 2000::/3 且非组播；两 SDK 拒绝短于 16 字节的密钥（weak_secret）；管理路由要求至少一个应用 read scope；收窄过滤先写安全日志 | 已修复（claude/suite-machine-webhooks，提交见 STATE），11 项变异全部被捕获；合入 feat/business-apps 待全量回归 |
| CR-050 | 已核实的 Admin 缺陷 D3–D20（scratchpad/admin-defects-verified.json，均为生产已有代码）：D5 高（主机 CLI 可签发不受空间批准约束的密钥/服务账户；服务账户可被重新登记挪到他人空间并扩权；目录接口读取不要求批准）、D6 高（CLI 停用空间应用不入安全日志，恢复备份后自动复活；CLI grant 绕过成员/启用/冻结规则）、D7 高（停用 Message 后账户域旧会话接口与页面重新开放，成为第二写入源）、D9 中（keys retire 无保护）、D10 高（部分已修）、D12 中（角色理由丢弃；委派者可收回更高角色）、D13 中（举报复核竞态）；其余中 D3/D4/D11/D14/D15/D18；低 D16/D19/D20 | 高：运维与治理路径的授权/恢复缺口 | 原有 | 分支 claude/admin-d9（基于 main 9bf71b9，含 claude/admin-defects）：D6 5d49d48+f7f3c7f、D7 第一步 1d5c03b、D5 中不需所有者决定的两部分 7695c54（生产只读计数：platform 服务账户 0、目录调用 0）、D13 e730e0f、D9 3255ad8、D12 49701fe；对抗式复审 wf_08d6102e-b4a（D6/D7/D13，D5 查找代理失败）确认无本轮引入的问题，另确认原有问题并修复：应用停用日志在事务内写入、平台级停用入日志 730a2c2，一次决定通知全部举报人并关闭全部待办 74aecd9，Message 回滚说明 c9896b6；均有变异验证。仍开放（原有，低）：组织冻结时 grant/enable 未拦截、launch_url 非 http 视为未登记、react/edit/report 与删除的竞态、同一举报人并发重复举报。所有者决定：D5 其余（CLI 是否仍可直接签发、既有未绑定凭据处置，admin-coverage §4.2 第 8 项）、D7 第二步（停用是否终止既有会话，第 7 项） | 全量 pytest 2991 通过 2 跳过（c6c2859）；第二轮对抗式复审 wf_38b4ee2e-238（D5/D9/D12/后续修复）与 HEAD c9896b6 全量门槛进行中；通过后推送 main 并部署 |
| CR-051 | 第三轮对抗式复核 feat/business-apps（wf_d5ea035b-e98，数据流/运行页/Webhook/流程引擎四维，每项独立驳斥核实）：确认 1 高（R3-DF-2 被移出的成员继续收到提交通知，含草稿标题与提交人）、9 中（R3-DF-1 extend 绕过外发检查；R3-DF-3 Agent 启动的流程把来源内容带回同一对话；R3-AR-1 他人关联文本经事件触发器外泄；R3-WH-1/2 Docs 未按所有者占用公共 DNS 解析槽、人可用多空间挤占；R3-FE-01 不可读行卡住每次 tick；R3-FE-02 失败结果读取不计来源；R3-FE-04 等待轮询耗费调用预算；R3-FE-05 事务内拒绝消耗尝试），8 低；R3-FE-06 被驳回 | 高/中：数据外流、治理视图误导、调度可用性 | 本分支引入 | 已修复（各有回归测试与变异验证）：DF-2 35a1acf、DF-1 9393aec、DF-3 0399b63、DF-4 4889c9a（FE-02 同时覆盖）、DF-5 80074ed、FE-01 ee17a74、FE-05 d6adc86、FE-03 b149b02、FE-07 480f79a、AR-1 45f0b2b、AR-2 94cc044、AR-3 d2f6069、AR-4 4e584a0、FE-04 中的缺陷部分 23e0c1d（无关事件按 poll_s/2 限频，读取对象的事件仍立即唤醒）；WH-1 130c8aa、WH-2 中向接收方计失败的部分 fa450b3、WH-3 4da948e（claude/r3-webhooks，合入 6261103）。所有者决定：WH-2 其余（16 个解析槽仍可被足够多的工作空间占满，影响现为延迟而非丢失；按人计、按应用分池或接受延迟）。FE-04 其余（状态检查是否计入 max_calls、预算耗尽是否走 on_error、默认 7 天等待撑不满）为所有者决定。修复工作流 wf_fda39802-367 的 C 组代理两次停滞，C 组余项由协调者完成 | 合并 main@7d690f1 后 cea2593：全量 4773 通过 1 失败（测试夹具重新登记扩大服务账户受众，被 D5 拒绝，a0cb8a5 改为单独登记后 24/24）；五守卫、typecheck、build、npm test、Python SDK 39、infra 8 通过；合入 Webhook 修复后 6261103：全量 4779 通过 2 跳过 0 失败；五守卫、typecheck、build、npm test、Python SDK 40、infra 8 通过；已推送，PR CI 37192910244 进行中；第四轮对抗式复核 wf_a736bbb9-19a 进行中 |
| CR-052 | 第四轮对抗式复核（wf_a736bbb9-19a，第三轮修复 + Webhook 修复 + 合并 main）：无高；确认中 5（R4-flow-engine-01 唤醒匹配过宽，同表任意记录事件仍立即唤醒；R4-correlation-admin-1 摘要搜索二次复杂度〔本轮引入〕；R4-correlation-admin-2 审批 list/get 向成员返回请求人关联文本〔原有〕；R4-forms-dataflow-1 duplicate 不查复制来源的携带来源〔原有〕；R4-webhooks-merge-2 解析池仍可被 8 个工作空间占满〔原有，WH-2 余项〕），低若干（引入：读者行未防护解析、extend 卡片先允许后拒绝、Docs 管理检查占位 15 秒、SDK 拒绝短部署密钥、拒绝本人平台关联编号；原有：FE-03/FE-07 余路径、Docs 与 suite 共用工作空间解析计数键）；R4-forms-dataflow-3 被驳回 | 中：数据外流、性能、可用性 | 第三轮修复引入 2 项，余为原有 | 已修复（各有回归测试与变异验证）：bd5611e 复合索引、e64005c 本人运行编号可沿用、25285f1 审批不返回关联、00dd5f4 事件须点名等待读取的全部 ID 且读者行防护、01b92cf 复制来源的携带来源、8fb2233 shown_to_link_holders、89910ed Docs 检查 5 秒、dcf1092 旧版验签只拒空密钥。未修（所有者决定）：R4-webhooks-merge-2 与 -1（解析池上限设计）；原有低项 R4-flow-engine-03/04 记录待排期 | PR CI 37192910244（6261103）通过；本轮修复后全量门槛进行中；第五轮针对性复核 wf_0d70d1fe-3bf 进行中 |
| CR-053 | 第五轮针对性复核（wf_0d70d1fe-3bf，第四轮修复）：无高；中 1（R5-correlation-1 DataPrep run.list/get 向其他成员返回启动者关联文本与操作运行编号〔原有，与审批同类〕）；低：引入 4（审批旧结果因去掉关联后指纹不符而重放失败；等待唤醒要求过滤字面量也被事件点名；Docs 检查时限不受发送时限约束；5 秒检查拒绝慢而有效的名称）与原有若干（检查名额总量、Standard Webhooks 验签对短部署密钥、Python 旧版验签拒绝 bytes、publish 同类卡片先允后拒、深层嵌套读者行） | 中：数据外露 | 原有 1，引入 4 低 | 已修复（各有回归测试与变异验证）：d12cad8 DataPrep 仅启动者可见、3988436 审批结果比较去掉关联、4bd04d6 唤醒目标只取 id/*_id 输入并防 RecursionError、aceaf1f 检查时限取两者较小。未修：5 秒检查对慢名称的拒绝与放弃的解析仍计额度（与 suite 相同，低）、其余原有低项记录待排期 | 独立复核（R6）又确认 1 中（3988436 让审批人的旧结果带着请求人关联重放给审批人）与 1 低（深层读者行在 _ids 递归出错），42adee5、a47b148 修复并变异验证；a47b148 全量 4788 通过 2 跳过、五守卫、typecheck、npm test 通过；业务应用在生产默认关闭（SYNWEVO_SUITE_APPS 未设置、Agent 需显式模型与密钥、计费关闭），合入 main 只部署代码与新增表，不对用户开放 |
| CR-054 | 业务应用上线验收（2026-10-05，隔离实例运行已部署代码、SYNWEVO_SUITE_APPS 与生产相同，真实浏览器 + API，三语言、390px、键盘、内存）：Forms 不通过（D1 高：自动保存失败时发布丢新字段；D2 高：重复明细显示 [object Object]；D3/D4 中），Approvals、Tables、DataPrep、Extract 有条件通过（中：审批模板可让请求永久卡住；表格字段设置手机溢出、关联记录无法按名称导入；DataPrep 计数显示 4.000000、≠ 过滤对缺失与空值不一致；Extract 截断 PDF 被当作部分结果、多页扫描 OCR 静默丢明细），另有低项若干；图标复核 1 中（窄屏返回链接溢出）5 低 | 高/中：数据丢失、结果错误、可用性 | 原有 | 全部修复（每项先写失败测试、变异验证）：Forms 8 项、Approvals 5 项、Tables 6 项、DataPrep 6 项、Extract 5 项、图标复核 5 项，合入 claude/app-icons；内存：DataPrep 5 万行 388MB、Extract 12 页 OCR 整容器 493MB，portal 上限主机已改 1536m（备份 compose.yaml.bak-20261004T174954Z），仓库同步 | 合并后：pytest 4817 通过 2 跳过；五守卫、brand_assets/app_icon_assets --check、typecheck、build、npm test、Python SDK 40、infra 8 通过；推送 main 部署中 |


### 2026-10-06 DeepSeek 发布专项（Codex）

| ID | 位置与问题 | 影响 | 归属 | 处理与证据 | 状态 |
|---|---|---|---|---|---|
| CR-055 | 计费/支付：欢迎额度每日容量、创建收银台与配置撤销、充值入账与拒付并发 | 高：多发额度、旧支付配置继续使用、竞争导致错误终态 | 原有待发布实现 | `fbf4e9c` 事务内复查与幂等状态保护；153项定向回归 | 已验证并随961acbf发布；正式Agent开放另见STATE |
| CR-056 | Agent 结果缺少草稿的认证内部链接；新增Forms get描述未同步生成契约 | 中：真实产物无法直接打开；CI契约门槛失败 | 原有链接缺口；本轮引入契约漂移 | fbf4e9c添加服务端注册路径，734736a重新生成OpenAPI；真实点击进入草稿、机器契约4项 | 已验证并随961acbf发布；正式Agent开放另见STATE |
| CR-057 | `agent/engine.py::_lost_workspaces` 对整个旧列表做哈希，新建Form后误封会话 | 高：合法写入已成功但回答失败 | 原有来源检查，真实DeepSeek验收暴露 | 734736a记录Forms列表原项ID，新增项可排除，原项/字段/访问改变仍拒绝；18新增/153关联回归及真实读→确认写→产物验证。其他读取与旧仅哈希来源保持严格策略，本修复不宣称解决所有可变来源的持续会话 | 已验证并随961acbf发布；正式Agent开放另见STATE |
| CR-058 | CI整场保留每测SQLite目录，完整运行到81%时ENOSPC | 高：阻止发布，丢失完整测试结果 | 原有测试基础设施容量问题 | 同20项Identity测试默认保留67.20MiB，清理对照6.11MiB；原生failed会删除setup/teardown失败现场，改进需等待全部三阶段成功再清理 | 本地11项及独立3项通过；961acbf CI全量镜像5130通过/5跳过，attempt2部署成功，生产镜像已核对 |
| CR-059 | 公开文案称“正在取得”DeepSeek书面答复，但没有已发送/收到记录 | 低：状态表述不准确 | 原有待发布文案 | 734736a简繁英改为“尚未取得”；另备询证草稿且明确未发送 | 已验证并随961acbf发布；正式Agent开放另见STATE |

- CR-058 收尾：完整 setup/call/teardown 全通过才删除原生 tmp_path；跳过/失败/覆盖fixture/symlink/会话模板保留。仅 disposable CI image 显式启用；11项 Python3.12 infra 检查通过，独立3项复核通过且源码哈希一致，无阻断。失败目录保留至 runner 销毁，不宣称已上传可下载 artifact。961acbf完整远端验证已通过；原deploy被平台取消后，恢复attempt2部署成功；验证结果复用原同SHA门槛。


### 2026-10-06 Admin 当前源码复核与修复

历史CR-050批次不代表当前HEAD。以下按f1dae29重新核对，证据见 `evidence/2026-10-06-admin-continuity`。

| ID | 位置及影响 | 来源 | 处理及验证 | 状态 |
|---|---|---|---|---|
| CR-060 / D11 | admin_operations普通停用可使组织/共享空间没有有效owner/admin；删除路径未验证空间继任者的组织成员资格 | 原有，高：治理不可恢复 | 共用ownership检查、事务内预览/执行复核、继任者版本绑定；127项及真实拒绝UI | 已验证并发布08d6700；Actions37430536566成功，08:31Z线上健康核验通过 |
| CR-061 / D13 | 举报审核的权限、审计和通知不在决定事务；创建举报可被审核超越，遗留pending待办或对已删除消息新建open举报 | 原有，中：状态与通知不一致 | 双向原子提交/锁内重鉴权；after_commit发布；确定性竞态及持久化失败回滚测试，真实审核与Store计数一致 | 已验证并发布08d6700；Actions37430536566成功，08:31Z线上健康核验通过 |
| CR-062 / D16 | API治理业务已提交后才写成功审计，失败可能留下未审计凭据变化；任务事件过早广播；启用后恢复可能再次停用未说明 | 原有，高资金/权限相关审计完整性，中恢复可解释性 | mutation事务覆盖本地治理/审计，网络调试例外；任务事件after_commit；三语言恢复说明；112+29关联及旧token回归、真实重认证/一次启用 | 已验证并发布08d6700；Actions37430536566成功，08:31Z线上健康核验通过 |

暂停断点：D4轮换重叠选择UI与D18事务内有界附件清理已完成本机定向验证，未提交/未部署；源码哈希、测试及关闭证据在evidence/2026-10-06-paused-followups。D20主动重投响应丢失后的请求幂等只完成调查，尚未改码；D18通知/桥接终态失败恢复仍未实施，不能自动重发SMTP结果不明项。用户要求本轮08发布后暂停。


### 2026-10-07 Webhook重试与附件清理

| ID | 位置及影响 | 来源 | 处理及验证 | 状态 |
|---|---|---|---|---|
| CR-063 / D4 | 控制台轮换签名密钥固定立即停用旧密钥，无法选择接收端切换窗口 | 原有，中：轮换期间可用性 | 默认沿用后端24小时，明确零/10分钟/1小时/24小时；三语言真实浏览器确认、取消与重置 | 已验证并发布8ba2956；Actions37619470838成功，14:39Z线上核验通过 |
| CR-064 / D18 | 过期未发送附件不由worker清理，清理前还可使用；单次LIMIT不能避免扫描全部历史 | 原有，中：生命周期与写锁占用 | 写锁内到期校验/绑定与清理互斥；既有分钟任务最多删除100行，保留共享blob；Store兼容补部分索引；13项最终关联/升级回归及30万行查询计划测量 | 已验证并发布8ba2956；Actions37619470838成功，14:39Z线上核验通过；通知终态失败恢复不在本项 |
| CR-065 / D20 | 控制台或SDK重投响应丢失后重复调用产生额外投递；受理与通知缺少外层事务约束 | 原有，中：意外重复副作用 | 复用core持久幂等、实际缓存资源鉴权、提交后唤醒；UI同确认与SDK同调用保留键，秘密操作不缓存；并发/撤权/冲突/回滚、三语言断线与重认证验收 | 已验证并发布8ba2956；Actions37619470838成功，14:39Z线上核验通过 |

证据位于 evidence/2026-10-07-webhook-reliability；所有测试数据隔离，未向外部Webhook接收者发送。旧客户端无键保留旧语义，新SDK需要支持重投幂等的服务端；返回旧收据不代表投递当前仍pending，当前状态使用GET查询。

### 2026-10-08 付费 Agent 充值准备

| ID | 问题及影响 | 处理及证据 | 状态 |
|---|---|---|---|
| CR-066 | Checkout固定三渠道，只有卡资格的商户也被要求微信/支付宝 | 治理版本选择非空子集、订单固定版本、个人/组织同源展示；历史兼容、幂等、签名入账与真实重认证浏览器，evidence/2026-10-08-payment-methods | 已验证并发布25b084f；Actions37724156180及线上核验通过，真实支付另待验收 |
| CR-067 | joinMessages单个已翻译错误覆盖来源为自身，二次翻译栈溢出；无request_id的网关错误可触发 | 保留单项原来源，基线RangeError复现、三语言与组合消息回归；507项前端通过 | 已验证并发布25b084f；Actions37724156180及线上核验通过 |

| CR-068 | 响应丢失后充值重试会创建多个订单，未知上游结果被误报失败 | 持久原始意图与并发唯一绑定、重新鉴权、前端刷新恢复、核实中状态；evidence/2026-10-08-reliability | 已验证并发布a41ea08，Actions37792532752成功 |
| CR-069 / D18 | 终态入队失败缺少可证明安全的管理恢复入口 | 新出生atomic-v1来源、SignedPreview/严格重认证/审计幂等；历史或上游结果不明仅可读，不直接寄信 | 已验证并发布a41ea08，Actions37792532752成功 |

### 2026-10-08 Agent 正式调用收尾

| ID | 问题及影响 | 处理及证据 | 状态 |
|---|---|---|---|
| CR-070 | 达到次数/金额上限在reserve_call前拒绝，留下无预留调用意图，使已知用量持续pending；默认1次与工具任务不匹配，提示无法区分金额/次数 | 锁内证明无ledger预留/无provider观察才释放计量状态，保留不可重放意图；终态恢复替换用量投影且不动资金。调用次数与金额独立解释，新报价建议6次仍由用户确认。两项原实现失败回归、132关联、415 Agent项（414初跑+1授权回环）、145前端及真实三语言/390px键盘验收通过 | 已验证并发布a41ea08，生产源码核对通过；旧任务恢复状态审计未执行，个人支出保持暂停 |

### 2026-10-09 Agent 历史分页

| ID | 问题及影响 | 处理及证据 | 状态 |
|---|---|---|---|
| CR-071 | 原有engine.list_conversations只按updated_at分页，时间相同的会话会漏页；旧load-more失败可覆盖新搜索，历史重复行未去重 | 复用core sealed_token，时间/ID复合位置绑定本人及筛选并限时；保留旧before。前端请求代次、取消、页内去重与失败保留；4项真实HTTP/Store回归，419 Agent检查（418初跑+1回环授权复验）、149前端及真实三语长列表/键盘/手机/刷新验证 | 已提交推送b2309ef；Actions37925001552完整运行镜像检查中，未宣称上线；evidence/2026-10-09-agent-history |

CR-070历史记录补正：2026-10-09只读已核实三个指定旧失败任务usage known、未发第二步released；本人预算实际v4 enabled，生产任务本身仍failed。见readonly-closeout证据，不再沿用上一行的未核实/暂停状态。

### 2026-10-09 支付渠道诊断

| ID | 问题及影响 | 处理及证据 | 状态 |
|---|---|---|---|
| CR-072 | 管理员只看到已选渠道，无法从产品读取商户默认配置状态；原生禁用检查按钮导致键盘焦点丢失（本轮浏览器发现） | 财务权限只读检查已保存版本、固定Stripe源站、有界分页/响应/并发、网络后权限/版本复核、未知状态及脱敏；UI保留焦点、单请求/取消、草稿提示与三语言。60支付关联+29角色矩阵+163前端、类型/构建/六守卫与浏览器通过；真实Stripe只读适配通过。evidence/2026-10-09-payment-readiness | 本地已验证，待提交/准确SHA CI/CD；未改生产支付配置或发起付款 |

| CR-073 | Docs新上传只受100MiB文件上限限制，默认25MiB扫描上限随后必然拒绝；浏览器来源列表未跟踪scanning状态，预览已有数据但仍显示安全检查中；手机模板标签固定34px导致英文换行遮挡 | 中：无效大文件落库和状态误导 | 既有Admin D15/Docs | App共享大小闸门覆盖服务/UI/API/导入/分片；精确有效字节上限公开；保留分片回执与取消，现有对象仍可读取；扫描及有重试期限状态刷新，永久失败不轮询；模板标签自适应、重新选同文件可重试 | 本地126后端+145前端、类型/构建/六守卫，真实隔离三语言/手机/混合上传/键盘/记录核对通过，尚未提交部署；evidence/2026-10-09-upload-admission |

## CR-074 · Docs扫描故障恢复（2026-10-09，原有，进行中）

- 位置：`apps/docs/synwevo_docs/services.py` `_scan_then_parse`、`resume_parsing`。
- 证据：重试延迟达到600秒后无限续期；恢复函数对所有scan_failed立即调用，包括尚未到retry_at和too_large终态。无文件级人工扫描重试API。
- 影响：持久故障反复占用解析线程，重启放大扫描负荷，用户缺少明确停止/恢复状态；隔离仍有效。
- 处理：在原扫描服务中限制自动尝试，保留累计次数/退避截止；用原权限与来源版本提供可审计的人工恢复，不允许重新接受恶意、加密或超限文件。验证重启、并发/重复请求、授权、SDK、真实浏览器。


### 2026-10-10 CR-073后续及CR-074验收

- CR-073审查发现脱敏后尺寸膨胀会在存储后整批抛413，掩盖前项成功并诱发重复：改为存储前逐项接纳检查，保留原partial结果契约，真实HTTP混合批次回归通过。
- d7d9201的CI拦截旧too_large detail新增字段；恢复旧filename/整数limit_mb/可选field契约，保留原test_refusal_details断言。最终54项通过，deploy已跳过，未以本地结果宣称上线。
- CR-074六次持久尝试、到期恢复、同进程防重、事务审计/版本竞争与隔离均已验证；浏览器发现中心预览误写“正在解析”，已按真实扫描/隔离状态修复。72/97/91/54关联批次、145前端、双SDK和三语手机/重启/键盘验收通过；批次有重叠不累加。
- CR-071b230及CR-072583准确CI和线上只读回执均完成。CR-074及上述兼容修复待新SHA完整CI/CD。证据scan-recovery、payment-readiness、agent-history。


## CR-075 · Agent会话切换与连接恢复（2026-10-10，原有，本地已验证）

- 位置：suite/web/src/apps/agent/useConversation.ts；调用方index.tsx的act/consentSend及Chat。
- 影响：中。路由变化到effect清理前旧内容/操作状态未绑定新会话；await refresh在旧请求繁忙时提前完成，操作按钮可早于新状态解锁；旧SSE error/reconnect回调引用可变source，可能关闭替代连接或伪报离线。没有证据说明后端授权或幂等失效。
- 独立期望测试编译实际hook，显式调度HTTP/React/SSE替身：旧实现3失败/2通过。修复保持只读和失败隐藏内容语义，路由绑定快照、共用下一次读取的等待承诺、连接实例校验；5项通过，154前端/类型/构建/六守卫及三语言桌面/手机真实隔离浏览器通过。慢速切换、读取失败隐藏与SSE恢复、刷新同ID、阅读位置保留，0新增turn；未新测减少动画。没有新模型调用或业务重放。

### 本轮CI修复的验证纠正

6e0287b由本轮缩小词库格式diff引入缺逗号，Actions38015163162在TS2695/TS7015处拒绝，未发布。早先本地检查在格式改写之前，原i18n-fix-verification.json已标明hash与检查不绑定，不得引用为该提交通过。最终分隔符和严格JSON要求均修复，最后源码编辑之后6项契约/163前端/根类型/账户构建通过；原门禁及断言保持。

## CR-076 · 会话尚未确认时的发送状态（2026-10-10，原有，本地已验证）

- Chat.tsx blocked未包含loading、error和configured未知，读取失败隐藏正文后仍可输入并尝试发送。隔离浏览器已观察失败页仍有composer；服务端鉴权未发现绕过，属于关键交互状态错误。
- 保留草稿可编辑、停止动作和持有有效快照时的SSE离线使用；只阻断未完成会话/模型设置读取或当前读取失败时的新发送。由Turing限定Chat及专项实现，浏览器与集成由协调者完成。

- CR-076完成：阻断loading/error/configured未知的新发送，保留新会话就绪与仅SSE离线的发送、可编辑草稿/取消。子任务旧实现4失败/1通过（仅对话日志），最终专项40通过；协调者159前端/类型/构建与真实隔离浏览器通过。失败时Enter后0turn/0quote，恢复后同草稿可发送；三语/390px/0溢出/控制台无错误。未作实际付费发送，未新测减少动画。

## CR-077 · Agent显式业务上下文（2026-10-10，本轮新增，本地已验证）

- 位置：agent/context.py、engine.py、quotes/consents/ops；Chat/ContextPicker/index。复用注册服务与sources_json，表单定义/表结构有界读取，正文不可由客户端伪造；quote/start同一指纹与事务。
- 独立审查发现高风险遗漏：手动重新选择已从受限空间复制来的目标资源，没有沿用普通工具读取的carried来源；隐藏复制回执和兄弟分支可丢失导出约束。4真实服务回归在修复前3失败/1通过。现prepare共用_carried_into，派发与来源展示只依赖目标访问，后续转出检查保留原始来源；修复后4通过，审查者复验关闭。未发布过缺陷版本。
- 最终171后端/174前端、根类型/Suite构建/六守卫及真实三语浏览器通过。1次隔离确认对应1任务/报价/尝试/结算；来源变化全部隐藏。生产外部调用/付款、个人文件上传、手动减少动态效果均未在本切片完成。

## CR-078 · Agent个人附件（2026-10-10，本轮新增，本地已验证）

- 位置：agent/files、file_routes、context/engine/quotes/consents、Docs ingest严格读取、core新增agent_files与个人账户删除、FilePicker与客户端。真实Docs解析通过server注入，通用受限解析进程由Extract与Agent共用，未反向导入应用或新建模型服务。
- Newton独立审查两项：24小时过期但清扫前可读取/挂接；旧账户服务终结删除不知道新表。修复即时过期检查（含worker/重试）与DB终结删除触发器，保留宽限期恢复；26项独立重跑及旧代码/旧连接/事务回滚/晚到结果脚本通过，无新finding。原始字节改为有界inline BLOB，在拥有事务内清除，避免共享blob孤儿；不声称SQLite/WAL/备份物理擦除。
- 浏览器发现前端fileRecord把服务端小数秒时间戳当非法值；上传已成功，界面显示unknown。修复时间戳有限非负数校验，计数仍严格整数；夹具改为小数秒并新增非法值回归。刷新列表恢复同一文件，未重传或重放任务。
- 最终248后端+21既有Agent/API三语契约、197前端、根类型/Suite构建/六守卫通过；既有断言保留。实际浏览器三语/桌面1280/手机390、真实上传/解析/完整长行/来源/下载哈希、报价取消、单次测试运行、刷新与删除依赖隐藏通过。生成源与日志指纹见agent-files/verification.json。模型/付款为隔离测试传输；未新测reduced-motion，不代替真实DeepSeek产物或付款验收。

## CR-080 · extract_context拒绝未进入Docs三语言门禁（2026-10-11，CR-078引入，已修复·本地已验证）

- 位置：apps/docs/synwevo_docs/ingest.py:42–60/132/136/145/158/160/216/219；门禁 apps/docs/web/tests/refusals.test.ts。影响：高（阻断发布）。证据：Actions38054090562 verify `not ok 20`，本地复现同11条。
- 调用链实读：仅 apps/suite/synwevo_suite/agent/files.py 文件worker调用；code落 `agent_files.error_code`；apps/suite/web/src/apps/agent/files.ts `fileErrorKey` → files.messages.ts 三语言，agent-files.test.ts 逐码断言。Docs工作台无路由到达。
- 处置：门禁新增一组API_ONLY决定（7句，含理由与Suite约束位置）；两处内部LimitError改为 `_context_engine_limit()` 直接抛 `context_limit_exceeded`（码/状态/原句不变，test_ingest_context 通过）。未删断言、未排除文件。
- 顺带：worker自身字数上限 `context_too_large` 与业务资料选择器同码，agentErrorText先查文件码会把业务资料“过大”说成文件——改用 `context_limit_exceeded`（文案contextLimit语义一致）；`parse_cancelled`（worker停止时）原落到“无法读取个人文件”通用句，改映射parseInterrupted；测试清单补 parse_cancelled。
- 验证：见 evidence/2026-10-11-ci-refusals/verification.json。待CI通过与部署后在生产只读复核。

## CR-081 · Suite前端测试共享Vite缓存的并发竞争（2026-10-11，原有，已修复·本地已验证）

- 位置：apps/suite/web/tests/*.test.ts（11处createServer）。影响：中（本地整套前端测试多例ENOTEMPTY，掩盖真实结果；CI未必复现）。证据：/private/tmp/synwevo-agent-budget-sources-frontend-20261010.log 与本树 `.vite/deps_temp_*` 残留。
- 根因：Node `--test` 多进程并行，各进程Vite客户端依赖优化器写同一 `node_modules/.vite/deps`（临时目录→rename→rmdir），不是测试逻辑缺陷，也与CR-080无关。
- 处置：`tests/_vite.ts testServer()`：独占mkdtemp cacheDir、`optimizeDeps.noDiscovery=true/include=[]`、close时删除自身目录；10个已提交测试与未提交的CR-079测试改用。不串行、不删测、不动全局。
- 验证：suite-web 201×2通过、0 ENOTEMPTY、tmpdir无残留；导出树197通过。后续：services/identity/web/tests 同模式未改（未观察到失败）。

## CR-079 · 报价前展示并核对所选来源（2026-10-11，本轮新增，已验证·本地+隔离浏览器）

- 位置：apps/suite/web/src/apps/agent/budgetSources.ts、BudgetDialog.tsx、budget.messages.ts；测试 tests/agent-budget-sources.test.ts、tests/agent-budget-dialog.test.ts。
- 审查结论：读取只用个人操作且与选择器同一快照校验；来源变化/删除/撤权/损坏整体阻止报价并给出重试与安全返回；offer与审阅共用AbortSignal，close/renew/unmount中止并以generation票据丢弃迟到结果；`sameIntent` 已含file_refs/context_refs，父组件意图变化时按钮禁用；服务端在quote/start仍重新核对（前端展示非唯一安全措施）。`personalFile` 英文与 fileCopy.open/contextCopy.personalFile 同源串一致（先注册者生效），已对齐避免误导。
- 验证：见 evidence/2026-10-11-agent-budget-sources/verification.json（205前端、三语/390/键盘/来源变化/计数/reduced-motion）。

## CR-082 · 懒加载报价对话框首次打开丢失焦点返回目标（2026-10-11，原有，已修复·探针验证）

- 位置：apps/suite/web/src/apps/agent/index.tsx（`lazy(BudgetDialog)` 经 App.tsx 路由级 Suspense）。影响：低-中（键盘用户首次打开后Escape焦点落到body；第二次正常）。证据：Playwright焦点探针 attempt1 BODY / attempt2 TEXTAREA；focusin/out日志显示块加载期间输入框失焦。
- 处置：对话框自带 `<Suspense fallback={null}>`，路由内容不再因块加载被隐藏；修复后两次均回到 `#agent-message`。既有Modal焦点逻辑未改。

## CR-083 · 取消/恢复链路独立审查（2026-10-11，MASTER §10.1/10.3、C7/C9；原有，三项已修复·本地已验证）

- 实读：engine `_cancel/_run/_execute/_decide/_resume_locked/_finish/recover/_api_history/_close_calls/_read_only_turn`、`useConversation`、Chat 停止按钮，以及既有测试（cancel_withdraws、restart_reconciles、stream resume、deepseek replay、incomplete charging、metering recovery、unknown usage）。
- 成立的不变量：停止后不派发下一步（循环首、工具前、`_execute` 入口三处检查，确认 UPDATE 带 `cancel_requested=0` 条件）；未知结果不盲目重试（`unknown`→`outcome_unresolved` 失败；重启按 op_runs 对账；重新生成分支只读 `regeneration_read_only`；DeepSeek 回放用持久记录关闭未答工具调用）；刷新/重连只按 seq 续流、不提交；`_finish` 以终态 WHERE 守卫，迟到完成不改坏终态；`_update_call` 只在 running 调用上写入真实结果。
- 修复（engine.py / Chat.tsx）：① `recover()` 把 `cancel_requested=1` 的进行中回答按用户决定收为 `cancelled`，原来一律记为 `interrupted` 失败并提示“可以重新生成”；② `_cancel` 对已无运行项的回答立即收尾时返回 `cancelled` 而非 `cancelling`；③ 会话投影新增 `turn.stopping`，Chat 在停止已受理但流未到停止点时禁用再次停止并以 `role=status` 说明“正在停止，等待当前步骤结束…”（三语）。文档 docs/agent-runtime.md 已写明。
- 未改动但记录：队列中（尚未开始）的回答在重启后也记为 interrupted 失败，重试走只读重生成，写操作任务需重新发送；多实例 worker 租约仍未实现（文档已说明）。
- 验证：test_agent 新增一例（waiting→cancel 立即 cancelled 且幂等、running+cancel_requested 的 stopping 投影、重启后 cancelled 无 error、无表单写入）；agent-chat 扩展 stopping 断言；suite-web 205 通过；apps/suite/tests 全量见 EVIDENCE。

## CR-084 · AG-ATTACH Office 文本文档（2026-10-11，本轮新增，本地+隔离浏览器已验证）

- 位置：apps/docs/synwevo_docs/ingest.py（`_context_docx/_context_xlsx` 及辅助、`CONTEXT_FORMATS` 加 docx/xlsx、`tables` 上限 1→32、OLE 容器判定）；Suite 前端 files.ts `fileSourceLabel`（Word 部件/段落/表格行/文本框、隐藏工作表）、files.messages.ts（Office 说明、来源标签、`unsupported` 文案改为“格式或内容”）、FilePicker 说明；测试 test_ingest_context.py 8 例、agent-files.test.ts 标签断言。
- 契约：DOCX 完整文字（正文/页眉页脚/脚注尾注/批注/文本框，表格按行，链接附网址，脚注标记去除）；任何图片→`context_needs_ocr`；嵌入对象/图表/SmartArt/公式/宏/ActiveX/墨迹→`context_format_unsupported`；OLE 加密包→`context_encrypted`。XLSX 每个工作表显示值（隐藏表标记）；无缓存结果的公式、图表、绘图、嵌入、透视、外链、批注、切片器、控件、宏→unsupported；图片→needs_ocr。全部或拒绝，不做部分读取；复用既有拒绝原句，Docs 门禁无新句。
- 审查要点：`para_segments` 的对象占位符不进入文本；AlternateContent 只读 Choice 分支避免文本框重复；嵌套文本框不重复计数；`tabular.read_xlsx` 的内置公式计算结果不被采信（有未缓存公式即拒绝）；member-import 的 `extract()` 路径未改。
- 验证：见 evidence/2026-10-11-agent-office-files/verification.json 与 browser-report.json（真实上传/解析/预览标签/拒绝文案）。未声称 OCR/图片、旧二进制格式、pptx。

## CR-085 · 账户前端测试共享 Vite 缓存的同类竞争（2026-10-11，原有，已修复·本地两次全量验证）

- 位置：services/identity/web/tests/*.test.mjs（12 处 createServer）。证据：本树 `services/identity/web/node_modules/.vite` 残留 5 个 `deps_temp_*`，与 CR-081 Suite 同一机制（并行进程共用依赖优化器输出目录）。
- 处置：`tests/_vite.mjs testServer(config)`：独占 mkdtemp cacheDir、关闭客户端依赖优化器、close 时删除自身目录，其余配置原样透传；12 个测试改用。`payment-methods.browser.mjs`（手动真实端口服务，不在测试 glob 内）未改。不删测、不串行、不改断言。
- 验证：`npm run test -w @synwevo/account-web` 两次均 163 通过、0 ENOTEMPTY，tmpdir 无残留（log sha256 5ca7dfd2bb9a4ece…/1e7bbaaf088775be…）。

## CR-086 · 业务资料类型扩展到审批/提取/数据处理（2026-10-11，本轮新增，本地+隔离浏览器已验证）

- 位置：apps/suite/synwevo_suite/agent/context.py（`Kind` 声明式读取契约、`_walk` 有界走查、前缀绑定）、ops.py 摘要；前端 types.ts/context.messages.ts/ContextPicker.tsx/budgetSources.ts；docs/platform/agent-context.md。
- 审查要点：仍只走各应用注册的同步只读 `.list/.get`（`snapshot_read` 拒绝写操作、异步与 `agent=never`），应用 ACL 原样生效；审批请求用 `view=all` 只是应用自己的可见范围（任何读权限成员可列工作空间请求，与应用页一致），`.get` 结果含依据快照与决定，受 24,000 字符上限；数据集行、填写提交、表记录仍不可选；前缀与 kind 不符在任何读取前被拒；游标/无搜索列表的走查最多 10 页并以 `limited` 如实提示，不猜总数。
- 验证：见 evidence/2026-10-11-agent-context-kinds/verification.json（1130 后端、206 前端、浏览器 8 类型/四对象选中/报价来源列表）。未端到端创建 extract_task 与 dataprep_run 对象（同一代码路径）。

## CR-087 · Admin A13 账户详情与安全时间线（2026-10-11，本轮新增，本地已验证）

- 位置：services/identity/synwevo_identity/admin_routes.py（`account_detail`、`TIMELINE_LIMIT`）、packages/core/synwevo_core/identity.py（`Identity.login_backoff` 只读）、services/identity/web/src/governance/AccountDetail.tsx、Admin.tsx 账户行菜单「查看详情」、governance/messages.ts；services/identity/tests/test_admin.py 两例、test_platform_access.py 矩阵行、web/tests/admin-account-detail.test.mjs；docs/platform/admin-coverage.md A13。
- 审查要点：权限走 `_admin(request, "platform.users.view")`（无平台权限 404、其他平台角色 403 并审计，与账户列表一致）；响应只有元数据——口令只给「是否已设置」，TOTP 只给开关与剩余恢复码数量，Passkey 只给名称/时间/同步标志，会话去掉 id（cookie 哈希）保留本人账户页同样的设备/方式/时间/地址；工作空间与组织名称随对应列表权限否则为 null，界面显式说明而不是隐藏整节；时间线只在持有 `platform.audit.view` 时返回（`actor_id=uid` 或 `object_kind='user' AND object_id=uid` 的最近 50 条，meta 只取 reason），否则 `permitted:false`——不成为审计日志的旁路；退避只读账户桶 `user:{email_key}`，不暴露地址桶；`outranks_caller` 与列表同一算法；查看不改状态、不审计（与模块内其他 GET 一致）。
- 风险/未做：未在浏览器实测对话框（SSR 三语言渲染、空态与省略说明已测）；Agent 内容安全停用状态与 A17 单设备撤销未并入；「谁查看过谁」若需追溯要另加审计。
- 验证：见 EVIDENCE「CR-087」与 evidence/2026-10-11-admin-account-detail/verification.json。

## CR-088 · 图片与扫描 PDF 经本地文字识别进入个人文件（2026-10-11，本轮新增，本地+隔离浏览器已验证）

- 位置：apps/docs/synwevo_docs/ingest.py（`CONTEXT_IMAGE_FORMATS`、`ocr_pages=5`、`_recognize_page`、`_context_pdf(ocr=)` 先分类后读取、`context_ocr_unsupported`/`context_ocr_failed`、`describe()` 的 `context_ocr`/`context_ocr_languages`）；apps/suite/synwevo_suite/agent/files.py（能力 `ocr`/`ocr_languages`/`ocr_pages`、`_recognition()` 按读取器缓存、子进程入口 `_extract_context` 只在读取器声明时传 `ocr=True`）、agent/context.py（文件项与可见来源加 `recognized`）；前端 files.ts/types.ts/FilePicker.tsx/ContextPicker.tsx 与 files.messages.ts/context.messages.ts；apps/docs/web/tests/refusals.test.ts 分类；docs/platform/agent-files.md。
- 审查要点：识别结果从不冒充已核对文字——每行来源 `recognized:true` 与最低词置信度、文件级汇总低置信度词数、界面「识别文字（未核对）」、模型负载 `recognized:true`；完整或拒绝不变：整份扫描 PDF 先分类再按预算拒绝（不先花时间识别再拒），文字层与图片混排页、含图片 DOCX 仍整份拒绝；没有引擎的部署图片直接 `context_ocr_unsupported`，文本格式不受 `ocr` 旗标影响；工作进程只在读取器声明能力时显式传旗标，旧读取器（测试替身/旧部署）调用形状不变；识别仍在既有 90 s/768 MiB 子进程内，像素上限沿用 Docs `LIMITS`，页面渲染沿用 `render_zoom`；引擎错误消息（页数、stderr、超时）不外泄，统一为无内容的 `context_ocr_failed`。
- 风险/未做：报价对话框来源列表仍只写「个人文件」（发送后的消息来源条有识别标记，浏览器已截到）；低置信度只计数不拒绝，由人核对；识别语言取部署可用的 chi_sim+eng，不可按文件选择；TIFF/WEBP 等格式与多帧图片未开放。
- 验证：见 EVIDENCE「CR-088」与 evidence/2026-10-11-agent-recognized-files/verification.json。

## CR-089 · Admin A17 账户单项动作：结束单设备登录、移除 Passkey、解除外部身份（2026-10-11，本轮新增，本地+隔离浏览器已验证）

- 位置：services/identity/synwevo_identity/admin_routes.py（三条 `POST /users/{uid}/...` 路由、`_security_notice`；A13 详情补回 sessions/passkeys/external_identities 的 id）；web/src/governance/AccountDetail.tsx（`AccountAction`、按钮、ActionDialog 叠加）、Admin.tsx 传入 `held`/`onChanged`、labels.ts 五个拒绝码措辞、messages.ts；tests/test_admin.py、test_platform_access.py 三行矩阵、web/tests/admin-account-detail.test.mjs；docs/platform/admin-coverage.md A17。
- 审查要点：与 A14/A16 同一护栏——`platform.users.manage`、`dangerous=True` 严格重认证、`_reason` 8–500 字、`_not_outranked` 在事务外与写锁内各复核一次、审计 meta 只含理由与计数/标识；复用本人路径的原语（`Sessions.revoke_device` 只结束该设备与它签发的令牌和依赖方会话；`Passkeys.remove` 自带 last_factor/last_super_admin 拒绝；解绑复用 `other_sign_in_methods`），管理员不能借此把人锁在门外——只能停用账户；通知走 `security.change`（设备用既有 `sessions_revoked` 文案，本人在其他设备看到「结束了其他设备的登录」）；前端按钮只在服务器会放行的条件下出现（持有 manage、未被越级、非应用身份），最终仍由服务器判定；单设备撤销不写安全日志并在路由文档说明原因。
- 风险/未做：推送订阅撤销无调用方未做；`last_factor` 原句含「你」在管理员视角不妥，前端 labels.ts 已改为管理员措辞；查看详情仍不审计。
- 验证：见 EVIDENCE「CR-089」与 evidence/2026-10-11-admin-account-actions/verification.json。

## CR-090 · Admin A11 管理员第二因素策略（2026-10-11，本轮新增，本地+隔离浏览器已验证）

- 位置：packages/core/synwevo_core/platform_access.py（`ADMIN_SECOND_FACTOR_KEY/MODES`、`admin_second_factor_mode`、`session_strong`、`admin_session_shortfall`、`require_platform` 第 4 步、`step_up()["second_factor"]`）；packages/auth-client/python `session_routes.describe_user`（`admin_locked` 按策略、`admin_lock`）；services/identity admin_routes（`/api/admin/session` step_up 字段、`GET/POST /security/second-factor`）；web：auth-client `session.tsx` 类型、`AdminStepUp.tsx`、`GovernanceTabs.AdminSecondFactorTab`、`SettingsTab`、labels.ts、messages.ts；tests：test_platform_access（新例 + 矩阵行 + roster 期望）、test_email_code_login 期望；docs/platform/admin-coverage.md A11。
- 审查要点：默认 off 时行为与之前完全一致（`admin_capable` 不变，`step_up_required` 原句不变）；required 时判定只看会话 `amr`（两类不同因素或 passkey_uv），口令重输不满足（`confirm_reauth` 只是把同一因素再记一次）；开启前置条件由服务器判定（至少一名可用最高管理员已登记 TOTP 或 UV Passkey），避免把所有管理员锁在管理之外；锁定只影响 `require_platform`（平台管理与 API 控制台），账户页、登记流程与 `/api/admin/session` 仍可用，所以被锁管理员总能自助解锁；策略写入与读取都在 meta，`sqlite3.Error` 时按 off 处理（与其他设置读取一致）；审计 meta 只含 before/mode/理由/已登记人数。
- 风险/未做：没有宽限期——开启后所有仅口令的管理员会话立刻被锁（界面在开启前提示本人与人数）；`caller_session_strong` 仅供提示；组织级与全体用户范围、Passkey 浏览器实测未做；`platform.security.manage` 未新增，沿用 config.manage。
- 验证：见 EVIDENCE「CR-090」与 evidence/2026-10-11-admin-second-factor/verification.json。

## CR-091 · 成员关系与邀请的 Webhook/事件（API-5，2026-10-11，本轮新增，本地已验证）

- 位置：packages/core/synwevo_core/member_events.py（六个触发器：memberships 的 INSERT/UPDATE OF role,can_export/DELETE，invitations 的 INSERT/UPDATE OF accepted_at/UPDATE OF revoked_at），store.py 安装；apps/docs/synwevo_docs/webhooks.py `EVENT_TYPES`/`EVENT_PREFIXES`；SDK 目录（python `validation.py`、typescript `synwevo-docs.d.ts` + `types-check.ts`）；`docs/integration/openapi.yaml` 重新生成（回调条目）；`docs/integration/api.md`、`docs/platform/api-coverage.md`；tests/integration/test_member_events.py。
- 审查要点：与来源文件事件同一模式——事件与行变化同事务（触发器），覆盖成员页、邀请接受、管理员认领/撤销、账户注销、安全日志恢复各路径，不依赖每个写入点记得发事件；负载只有标识与变化事实（workspace_id、user_id、role、can_export、previous_*、invitation_id、expires_at），不含邮箱、姓名、邀请令牌；tenant 为工作空间 id，只有该空间的凭据/订阅可见（测试另一空间只见自己）；`can_export` 用 `json(CASE …)` 写成 JSON 布尔；邀请过期不是行变化不发事件，`invitation.revoked` 只在未接受时发；旧代码不认识的类型按既有回滚语义忽略。
- 风险/未做：新空间创建与账户注销也会产生 member.added/removed（真实变化，已在文档说明）；恢复安全日志回放的成员变更同样发事件（它们确是状态变化，但订阅方会在恢复时收到一批）；模板 Webhook 事件仍未做；`job_events` 中的 user_id 在账户注销后保留（与审计一致，标识非内容）。
- 验证：tests/integration/test_member_events.py 2 例通过；apps/docs test_file_events（含 SDK 目录一致）、test_api_docs（openapi 同步）、事件流/订阅测试通过；Python SDK 55 通过；TypeScript SDK 48 通过；五守卫通过。

## CR-092 · 模板与组织模板库的 Webhook/事件（API-5，2026-10-11，本轮新增，本地已验证）

- 位置：packages/core/synwevo_core/template_events.py（六个触发器：templates 的 INSERT / UPDATE OF head / UPDATE OF deleted，organization_templates 的 INSERT / UPDATE OF deprecated_at，organization_template_releases 的 INSERT WHEN release_no > 1），store.py 在模板与组织模板表之后安装；apps/docs/synwevo_docs/webhooks.py `EVENT_TYPES`/`EVENT_PREFIXES`（`template.*`）；SDK 目录（python `validation.py`、typescript `synwevo-docs.d.ts` + `types-check.ts`）；`docs/integration/openapi.yaml` 重新生成（回调条目）；`docs/integration/api.md`、`docs/platform/api-coverage.md`；tests/integration/test_template_events.py。
- 审查要点：与来源文件/成员事件同一模式——模板从上传、字段编辑、恢复、跨空间复制、内置编辑器、发布多处写入，触发器保证每条路径都发事件且与行变化同事务；负载只有标识与版本号（template_id、kind、format、head、previous_head、publication_id、organization_id、release_no、version_no），不含模板名、文件、字段名或备注；tenant 为模板所在空间（发布事件为发布空间 `source_tenant`，后续版本经 publication 子查询取得），组织内另一空间不可见；`template.updated` 只在 head 真正变化且未删除时发，`template.deleted` 只在 0→1 的软删除边沿发，`template.released` 只对 release_no>1 发（第 1 次发布由 `template.published` 承担）；旧代码不认识的类型按既有回滚语义忽略。
- 风险/未做：恢复安全日志回放与跨空间复制产生的 `template.created` 是真实行变化（订阅方恢复时会收到一批）；弃用取消（deprecated_at 回到 NULL）不发事件（当前产品没有取消弃用的入口，若新增需补触发器）；没有界面变化，未做浏览器验收。
- 验证：tests/integration/test_template_events.py 1 例通过（创建→新版本→发布→再新版本→后续版本→弃用→删除的完整序列与数据；feed 与 outbox 不含备注/文档文字/夹具名；另一空间凭据只见空）；apps/docs test_file_events（含 SDK 目录一致）、test_api_docs（openapi 同步）、事件流/订阅与成员事件测试 38 通过；Python SDK 55 通过；TypeScript SDK 48 通过；tsc 通过；五守卫通过。

## CR-093 · Admin A20 删除请求：到期、阻塞规则、重发取消链接、立即处理到期删除（2026-10-11，本轮新增，本地+隔离浏览器已验证）

- 位置：services/identity/synwevo_identity/profile.py（`public_link` 共享邮件链接构造；`deletion_blockers`/`deletion_state`/`deletion_counts`）；account_routes.py 的 `link` 改为调用共享函数；mail.py `deletion_mail(resend=True)` 三语言新变体；admin_routes.py（overview `account_deletions`、users 列表 `deletion_due_at`、A13 详情 `deletion`、`POST /users/{uid}/deletion/cancellation-mail`、`POST /account-deletions/finalize`）；account-web：Admin.tsx 概览卡片与行菜单/状态列、governance/AccountDetail.tsx「注销申请」段与 `DELETION_BLOCKER_LABELS`、access.ts `cancellationMail` offer、labels.ts 两个错误码、messages.ts 24 条；tests：test_admin.py 1 例、test_platform_access.py 矩阵 2 行、test_mail_catalogue.py、admin-account-detail.test.mjs、admin-access.test.mjs；docs/platform/admin-coverage.md A20 行。
- 审查要点：管理员不能代为取消注销——重发只把新链接寄到本人邮箱，页面上由本人确认；新链接 `expires_at` 等于原删除到期时间（绝不延长），旧未用链接同事务作废；响应与审计只含 mail_id/due_at，不含令牌；速率限制把本人申请时的那封也计入（一小时 3 次、间隔 1 分钟），所以申请后一分钟内重发会被拒（浏览器验收实测并留证）；重发走 `_write` 写锁 + 严格重认证 + 越级检查，与其他 support 动作一致；终结路由直接复用 Sweeper 的幂等 `finalize_deletions`（内部自带事务，故不嵌套 `_write`），不会让任何申请提前到期，失败以 503 `finalize_failed` 报告并审计 error；阻塞规则只读取不决定（`check_ownership_continuity` 在事务外调用仅作诊断，返回码不含成员信息）；详情块对旧服务端缺字段时不渲染。
- 风险/未做：`deletion_counts.pending` 含逾期者（标签改为「待删除账户」以免误读）；逐账户终结未做；`_write` 把 4xx 状态拒绝（如 429）也记为 `admin.access.denied`，与既有路由一致；取消弃用/恢复宽限期等政策功能不在范围。
- 验证：test_admin 相关 5 例通过（新增 1）；test_mail_catalogue + test_self_service 55 通过；权限矩阵 30 通过；identity 全量 1552 通过（21:04–21:07Z）；integration 全量 1355 通过（21:08–21:12Z）；account-web typecheck/167 测试/build 通过；五守卫通过；浏览器验收见 evidence/2026-10-11-admin-account-deletions。

## CR-094 · Admin A17 推送设备：按账户列出、撤销全部推送订阅（2026-10-11，本轮新增，本地+隔离浏览器已验证）

- 位置：services/identity/synwevo_identity/admin_routes.py（A13 详情 `push_subscriptions`；`POST /users/{uid}/push-subscriptions/revoke`）；packages/core/synwevo_core/notifications.py `SECURITY_WHAT.push_revoked`（三语言）；account-web `governance/AccountDetail.tsx`（「推送设备」段、`pushDeviceText`、动作 kind `push`）、messages.ts 13 条；tests：test_admin.py 1 例、test_platform_access.py 矩阵 1 行、admin-account-detail.test.mjs；docs/platform/admin-coverage.md A17 行。
- 审查要点：详情只给设备 UA、时间、失败次数与是否仍可接收（`active_for` 按创建会话是否存活判断），绝不返回端点（能力 URL）或密钥；撤销复用 `PushSubscriptions.revoke_all_for_user`，原因 `admin_revoked`，幂等（数量为 0 时不发通知）；不触碰会话与登录方式；`users.manage` + 严格重认证 + 理由 + 越级检查，与 A17 其他动作一致；本人收到 `security.change`（新键在目录中有三语言，旧 zh 句映射不受影响）。
- 风险/未做：管理员不逐台撤销（本人账户页可逐台移除、发送测试）；设备 UA 解析与账户页同一规则（Chrome/Safari/… · OS），其余显示「浏览器」。
- 验证：test_admin + 矩阵相关 15 例通过（新增 1 例 + 1 行）；push/notification 测试 58 通过；account-web typecheck/167 测试/build 通过；五守卫通过；浏览器验收见 evidence/2026-10-11-admin-push-devices（identity/integration 全量在推送前随批复跑）。

## CR-095 · 委托令牌最近使用（API-1「最近使用」缺口，2026-10-11，本轮新增，本地已验证）

- 位置：packages/core/synwevo_core/store.py `OAUTH_TOKEN_COLUMNS`（`oauth_tokens.last_used_at`，加入列迁移循环）；oidc.py `Grants.load_access` 成功加载后最多每分钟写一次 `last_used_at`（与 api_keys 规则一致）；oauth_consent.py `Consents.list_for_user` 每条授权带同一 (user, client, workspace) 令牌的 `MAX(last_used_at)`；provider_routes.py `/api/auth/connected-apps` 返回 `last_used_at`；account-web `ConnectedAppsSection.tsx` 显示「最近使用 {date}」/「尚未用它访问过」（account.messages 三语言）；tests：test_oauth_consent.py（首次使用、分钟内不重写、回拨后再写）、tests/integration/test_suite_machine_flow.py（真实业务操作调用后授权显示最近使用）；docs/platform/api-coverage.md「最近使用」行改为已做。
- 审查要点：写入发生在校验路径（Docs/目录/业务应用三条机器入口与 userinfo 都经 `load_access`），每令牌每分钟至多一次 UPDATE，不改变任何授权判断；只读列表聚合不暴露令牌；回滚时旧代码忽略新列；控制台不列委托令牌（属于本人，不是空间凭据），按客户端的委托调用仍可在调用记录按 client_id 查询。
- 风险/未做：刷新换发的新令牌 `last_used_at` 为空，列表按 MAX 聚合不受影响；浏览器验收未做（仅账户页一行文案，API 与集成测试覆盖）。
- 验证：identity `-k "oidc or api_platform or machine or consent or token or connected"` 173 通过；integration `-k "machine or oauth or consent or delegated or token"` 59 通过；Docs `-k "machine or delegated or oauth or bearer"` 14 通过；account-web typecheck/167 测试/build 通过；五守卫通过。

## CR-096 · Admin A13 详情并入 Agent 内容安全暂停状态（2026-10-11，本轮新增，本地+隔离浏览器已验证）

- 位置：services/identity/synwevo_identity/admin_routes.py（A13 详情 `agent_access`：是否暂停、理由、操作者邮箱、关联举报、时间）；admin_agent_safety.py 的 suspend/lift 审计 meta 补入 `reason`；account-web `governance/AccountDetail.tsx`「Agent 使用权限」段与动作 kind `agent_lift`（复用 `/api/admin/agent-safety/users/{uid}/lift`）、messages.ts 7 条；tests：test_admin.py 1 例、admin-account-detail.test.mjs；docs/platform/admin-coverage.md A13 行。
- 审查要点：详情只给管理员写下的理由与身份、关联举报 id，不含任何会话内容；恢复复用内容安全模块的路由与权限（`platform.users.manage`，无严格重认证——与该模块一致，ActionDialog 仅在服务端要求时才提示重认证）；暂停仍在内容安全页进行（带举报关联）；补审计理由后，账户时间线的「理由」列对 agent_safety 事件不再为空。
- 风险/未做：详情不提供暂停入口；`created_by='journal'`（恢复回放）时操作者为空。
- 验证：identity 相关 15 例 + 补审计后 4 例通过；account-web typecheck/167 测试/build 通过；五守卫通过；浏览器验收见 evidence/2026-10-11-admin-agent-suspension。

## CR-097 · Admin A27 注册诊断 + 被限流注册写审计（2026-10-11，本轮新增，本地+隔离浏览器已验证）

- 位置：services/identity/synwevo_identity/registration.py `_throttled`（IP 与地址两种窗口拒绝都写 `auth.registration.throttled`，actor anonymous，object_kind registration，object_id 为规范化地址，meta 仅 scope/retry_after；对调用方的回答不变）；admin_routes.py `GET /diagnostics/registration`（`platform.users.view`；阶段表 validation/challenge/throttle/account/token/mail/activation，事件与人机验证随 audit.view、邮件随 mail.view，各自以 permitted 标明）；account-web `governance/RegistrationDiagnostics.tsx`（挂在账户页底部）、messages.ts 60 条；tests：test_admin.py 1 例、test_platform_access.py 矩阵行 + `RISK_STATE_REFUSALS`（无地址 400 `bad_email`）、admin-registration-diagnostics.test.mjs 3 例；docs/platform/admin-coverage.md A27 行。
- 审查要点：令牌只给签发/到期/使用时间，绝不给值或哈希；邮件只给 `_mail_row` 元数据（无正文）；审计事件只带 scope/retry_after/mail_error 三个键；人机验证只能按来源地址与时间窗关联（挑战发生在账户存在之前），界面写明口径；限流状态直接读 `auth_attempts` 不计数；诊断本身不写审计（只读）；被限流的回答对调用方仍是同样的 202，避免借此确认地址存在。
- 风险/未做：校验失败（400）不写审计，避免匿名刷量把日志填满；服务商回执以外的投递信息没有；人机验证关联是启发式（同地址 ±15 分钟）。
- 验证：identity 相关 8 例 + 注册/挑战/账户安全 66 例通过；account-web typecheck/170 测试/build 通过；五守卫通过；浏览器验收见 evidence/2026-10-11-admin-registration-diagnostics。

## CR-098 · Admin G19 限流状态：查看与带理由解除（2026-10-11，本轮新增，本地+隔离浏览器已验证）

- 位置：packages/core/synwevo_core/identity.py（`throttle_id`、`Identity.throttle_state`/`lift_throttle`、`login_backoff` 带 `throttle_id`）；services/identity/synwevo_identity/admin_routes.py（`THROTTLE_KINDS` 家族表、`GET /security/throttles`、`POST /security/throttles/lift`）；account-web `governance/GovernanceTabs.tsx`（`ThrottleTable`/`ThrottlesTab`，挂在平台配置页）、`AccountDetail.tsx`（登录退避段「解除登录退避」，动作 kind `backoff_lift`）、labels.ts 两个错误码、messages.ts 36 条；tests：test_admin.py 1 例、test_platform_access.py 矩阵 2 行、admin-throttles.test.mjs 2 例；docs/platform/admin-coverage.md G19 行（D10 行同步更正）。
- 审查要点：桶名可能含来源地址或邮箱键，列表只给 sha256 前 24 位作为句柄，解除按句柄在服务端匹配，原始地址不出服务器；账户家族的邮箱只对持 users.view 者显示，审计以用户为对象；慢退避（user:/reauth:）按计数等待、固定窗口按 blocked_until，`throttle_state` 只读不计数；解除只删除该桶（下次尝试从零开始），不触碰口令/会话/权限；权限沿用 config.view/config.manage（与 A11 一致，不新增权限）；解除需严格重认证 + 理由 + 越级检查。
- 风险/未做：无批量解除；地址家族的 ip 桶对攻击者的保护在解除后立即消失（这是解除的含义，界面已写明）；未新增 `platform.security.manage`。
- 验证：identity 相关 11 例通过；account-web typecheck/172 测试/build 通过（含 refusals 门禁）；五守卫通过；浏览器验收见 evidence/2026-10-11-admin-throttles。

## CR-099 · Admin A49 有效访问解释（2026-10-12，本轮新增，本地+隔离浏览器已验证）

- 位置：services/identity/synwevo_identity/admin_routes.py `GET /users/{uid}/access`（七道闸门 + `Apps.check_access` 裁决 + `consistent`）、A13 详情工作空间行附带 `apps`（已启用应用 id/名称）；account-web `governance/AccountDetail.tsx`（`ExplainTarget`、工作空间行「解释访问」、`AccessExplanationBody/Dialog`、`GATE_LABELS` 等）、labels.ts 两个错误码、messages.ts 36 条；tests：test_admin.py 1 例、test_platform_access.py 矩阵 1 行 + 状态拒绝、admin-account-detail.test.mjs 1 例；docs/platform/admin-coverage.md A49 行。
- 审查要点：解释不以该用户身份行事，只读；闸门的成员资格以 `workspaces_for` 为准（与真实闸门同源，包含组织成员要求）；最终裁决直接调用真实 `check_access`，并以 `consistent` 暴露任何偏差而不是掩盖；按应用种类（个人/组织/门户）跳过不适用的闸门，与 `_check_platform` 一致；邮箱与空间名随 users.view / workspaces.view；权限用 `platform.roles.view`（审计员可读、支持不可读），与 A5 一致。
- 风险/未做：不解释机器凭据；冻结空间时真实句子是通用句（闸门表点名）；组织冻结场景仅后端测试覆盖。
- 验证：identity 相关 11 例通过；account-web typecheck/173 测试/build 通过；五守卫通过；浏览器验收见 evidence/2026-10-12-admin-effective-access。

## CR-100 · Admin A32 第二因素普及统计（2026-10-12，本轮新增，本地+隔离浏览器已验证）

- 位置：services/identity/synwevo_identity/admin_routes.py `_second_factor_adoption`（概览 `second_factor`）；account-web Admin.tsx 概览卡片、messages.ts 7 条；tests：test_admin.py 概览断言 + 1 例。
- 审查要点：只对正常个人账户计数（排除应用身份、停用/待删/已删），动态验证码只算已确认的，安全密钥只算未删除的，恢复码只看是否还有未用的——全部是 COUNT，不读 `secret_enc`/`code_hash`；最高管理员一栏复用 `PlatformAccess.usable_super_admins` 与 `step_up()["second_factor"]`，与 A11 策略卡口径一致。
- 风险/未做：无按组织/空间细分；概览请求多了七次计数查询（索引齐全，账户规模内可忽略）。
- 验证：test_admin 相关 2 例通过；account-web typecheck/173 测试/build 通过；五守卫通过；浏览器验收见 evidence/2026-10-12-admin-second-factor-adoption。

## CR-101 · API 调用记录接受并记录 W3C traceparent（API-1「全链路回显」缺口，2026-10-12，本轮新增，本地已验证）

- 位置：packages/core/synwevo_core/http.py（`trace_id_of`：校验 `version-trace_id-parent_id-flags`，版本 ff、全零 trace/parent 视为无；网关把 `request.state.trace_id` 交给 `record_call`）；api_platform.py（`CALL_COLUMNS` 新增 `trace_id`，`record_call(..., trace_id)` 只接受 32 位十六进制）；services/identity/synwevo_identity/api_platform.py `/calls?trace_id=`（精确匹配，小写化）；控制台调用记录页「追踪编号（traceparent）」筛选与行内显示（api-platform.messages 2 条）；docs/integration/api.md 请求编号段落、docs/platform/api-coverage.md 行 38；tests/integration/test_api_platform.py 新增 1 例。
- 审查要点：非法 traceparent 不改变请求结果也不落库（只影响元数据）；trace_id 与平台自己的 `X-Request-ID` 并存、互不替代；列为回滚安全的追加列；筛选仍是对存储元数据的精确匹配，不对用户输入做 LIKE；任务与事件仍只带 `request_id`（文档写明）。
- 风险/未做：不回传 `traceresponse`；任务/事件不带 trace_id；无浏览器验收（控制台只多一个筛选框与一行小字，类型检查与前端测试覆盖）。
- 验证：integration 相关子集 355 通过（含新例）；Docs API 子集 71 通过；account-web typecheck/173 测试/build 通过；五守卫通过。

## CR-102 · Admin A11 第二因素策略的宽限期（2026-10-12，本轮新增，本地+隔离浏览器已验证）

- 位置：packages/core/synwevo_core/platform_access.py（`ADMIN_SECOND_FACTOR_GRACE_KEY`、`ADMIN_SECOND_FACTOR_GRACE_MAX_DAYS=30`、`admin_second_factor_grace_until`；`admin_session_shortfall` 在宽限期内不返回 `second_factor`）；services/identity/synwevo_identity/admin_routes.py（策略 GET 带 `grace_until/grace_max_days`，POST 接受 `grace_days`（整数 0–30，否则 400 `bad_grace_days`），required 且 >0 时写 `grace_until=now+days`，否则删除；审计 meta 带 grace；`/api/admin/session` 的 `step_up.grace_until`）；packages/auth-client/python `describe_user` 新增 `admin_grace`（仅对会被锁的会话）；account-web：策略卡宽限期输入与当前宽限期行、步骤卡（AdminStepUp）宽限提醒（已登记/未登记两种文案 + 登记入口）、session.tsx 类型、labels/messages；tests：A11 测试扩展、email-code 精确形状更新。
- 审查要点：宽限期只推迟锁定，不改变「至少一名可用最高管理员已登记」的开启前提；提醒只对 required 且会话不强的管理员出现（强会话不打扰）；期满立刻按原规则锁定（无额外状态机）；关闭策略同时清除宽限期；再次保存 required 可重设宽限（UI 的保存按钮按此启用）。
- 风险/未做：组织级/全体用户策略仍未做；宽限提醒只在账户页步骤卡（管理页顶栏不另加横幅）。
- 验证：identity 策略/步骤子集 36 通过，`me/session/login` 子集 975 通过，integration 登录/会话子集 182 通过；根 typecheck、account-web 173 测试/build 通过；五守卫通过；浏览器验收见 evidence/2026-10-12-admin-second-factor-grace。

## CR-103 · Admin G24 管理操作目录（2026-10-12，本轮新增，本地+隔离浏览器已验证）

- 位置：services/identity/synwevo_identity/admin_actions.py（`SCOPES` 9 个范围、`ADMIN_ACTIONS` 47 个 `admin.*` 动作，三语言标签，`catalogue()`）；admin_routes.py `GET /audit/actions`（`platform.audit.view`，只读）；account-web Admin.tsx 审计页（筛选下拉按范围分组列出具体动作、行内显示名称+代码，取服务端标签按读者语言显示）；tests：test_admin.py 1 例（扫描 identity 与 core 包全部 .py 的 `"admin.…"` 字面量 ⇔ 目录集合相等；每项有范围与三语言；路由形状；无权限 404）、矩阵 1 行；docs/platform/admin-coverage.md G24 行。
- 审查要点：目录与代码的一致性由测试强制（新增动作未入目录即失败，过时条目同样失败）；标签只说动作本身，不说结果与对象；三语言随服务端返回，前端不需再维护目录；筛选仍走既有 `action LIKE code%`，选择具体动作即精确到该动作及其子动作。
- 风险/未做：G24 的「同一事务写审计」与「统一前后值」仍未做；API 控制台 `api_platform.*` 族以动态后缀审计，未逐个命名；扫描只覆盖 `services/identity` 与 `packages/core`（`admin.*` 只在这两处出现）。
- 验证：identity 相关 16 例通过；account-web typecheck/173 测试/build 通过；五守卫通过；浏览器验收见 evidence/2026-10-12-admin-action-catalogue。

## CR-104 · Admin A5 权限模拟（2026-10-12，本轮新增，本地+隔离浏览器已验证）

- 位置：services/identity/synwevo_identity/admin_routes.py `GET /users/{uid}/platform-access/simulate`（change=grant|revoke、role；400 `bad_simulation`）；account-web `governance/PlatformAccess.tsx`（`Simulation`、`SIMULATION_REFUSALS`、`SimulationSummary`、对话框内「模拟一次变化」折叠块）、labels.ts 错误码、messages.ts 20 条；tests：test_platform_access.py 1 例 + 矩阵 1 行 + 状态拒绝、admin-simulation.test.mjs 2 例。
- 审查要点：模拟只读：由 `explain()` 的现状、`ROLES` 目录与计入规则（账户被阻、邮箱未验证则新授予不计入）算出前后集合，不写任何表（测试断言授予表行数不变）；可行性复用 `_assign_refusal`（与真实授予/撤销同一拒绝顺序：自我授予规则先于 already_held），再补 already_held / not_held；按设计不模拟业务操作，也不列出无权对象。
- 风险/未做：`last_super_admin` 分支在正常调用路径里不可达（可用最高管理员对他人模拟时自己就是另一位），保留为防御；业务操作模拟不在范围。
- 验证：identity 相关 10 例通过；account-web typecheck/175 测试/build 通过；五守卫通过；浏览器验收见 evidence/2026-10-12-admin-permission-simulation。

## CR-105 · Admin G24 写入与审计同事务（2026-10-12，本轮新增，本地已验证）

- 位置：services/identity/synwevo_identity/admin_routes.py `finalize_account_deletions`（扫描与审计放入同一 `store.tx()`，Store 事务可重入；审计失败则回滚终结，由下一轮扫描重做）；services/identity/tests/test_admin.py `_audit_insert_fails` + 参数化测试（8 条路由）。
- 审查要点：故障注入只针对该动作 result=ok 的审计 INSERT（`Store.execute` 包装，与既有 model provider 测试同法），其余 SQL 照常；断言三件事——响应 ≥500、状态与注入前完全相同（会话仍有效/限流桶仍在/策略不变/订阅仍在/无新邮件且旧链接仍未用/账户仍 pending_delete/空间仍 pending_claim 且无成员）、该动作无新的 ok 审计行；并断言故障原文不进响应、响应含 request_id。源码扫描确认直接写审计的 11 条路由与 claim/unclaim 均在事务内。
- 风险/未做：统一前后值仍由各路由自定；API 控制台（`api_platform.py`）的事务/审计关系未纳入本测试（其 `mutation()` 有自己的边界测试）；Docs/Suite 的管理写入不在本包。
- 验证：test_admin 相关 9 例通过；identity `-k admin` 子集 666 通过；五守卫通过。无界面变化，未做浏览器验收。

## CR-106 · WP1 Forms 桌面宽屏与英文/繁体走查（2026-10-12，本轮新增，本地已验证）

- 位置：apps/suite/web/src/apps/forms/Pages.tsx（表单库标题源文「表单库」；卡片计数拆为 `{n} 个请求` / `{n} 份提交` 两条复数消息）、apps/suite/web/src/apps/forms/messages.ts（「表单」单数 Form；两条新复数条目）、packages/ui/src/i18n-core.ts（`pluralCount`：显式 `count`，否则唯一数字参数）、packages/ui/tests/i18n.test.ts、packages/auth-client/web/src/PlatformNav.tsx（无所有者前缀时不写分隔符）；
- 审查要点：目录「先注册者胜」意味着通用源文在多个应用间共享，页面标题应使用专属源文；复数选形此前只看 `count`，而现有调用全部以 `{n}` 传数，修正为按唯一数字参数选形不改变任何带 `count` 的现有行为（显式 `count` 仍优先），多数字参数无 `count` 时保持 other；
- 风险/未做：`check_design`/`check_component_names` 对此无影响；窄屏与 Docs/Message 适配（WP7）不在本轮；走查为 Forms 应用，Tables/Approvals 等其他 Suite 应用的英文/繁体宽屏走查仍待做；
- 验证：六个前端工作区测试 612 通过（含 account-web 刷新门禁 175）；根 typecheck/build 通过；五守卫通过；隔离浏览器三语言走查重跑通过（evidence/2026-10-12-forms-wide-locales）。

## CR-107 · Admin G24 统一前后值（2026-10-12，本轮新增，本地已验证）

- 位置：packages/core/synwevo_core/audit_change.py（`change`）；services/identity/synwevo_identity/admin_routes.py（14 处审计 meta 加入三键，第二因素路由在写入前读取宽限期截止；移除安全密钥前后计数经 `Passkeys.count`）；admin_agent_safety.py（暂停前先读是否已暂停）；packages/core/synwevo_core/platform_access.py（授予/撤销/系统授予/系统撤销/运维角色清除：`before`/`after` 由角色列表改为 `{"roles": […]}` 快照，响应体不变）；packages/core/synwevo_core/organizations.py（状态变更）；services/identity/web/src/governance/audit.ts + Admin.tsx（「变化」列，colSpan 7）；
- 审查要点：三键只加不减——各路由原有的 `mode`/`sessions`/`revoked` 等键保留，读旧记录的代码不受影响；平台角色审计的形状变化只影响 meta（三处测试断言随之更新），HTTP 响应仍返回列表；`change` 在写入之前读取旧值的地方（宽限期、是否已暂停、安全密钥数）都在同一事务内；前端对缺少 `changed` 的旧记录按键值差异推导，对旧的标量 `before`/`after` 不显示为变化；
- 风险/未做：模型提供方/成本/价格发布保留各自完整快照（无 `changed`）；API 控制台 `api_platform.*` 的 meta 由各动作自带，未纳入；Docs/Suite 的管理写入（G26）不在本轮；「变化」列的值为原始值（`true`/`frozen`），未做本地化；
- 验证：相关 Python 套件 305 passed, 0 failed（进度行计数；-q 汇总行未输出）；account-web typecheck + 177 通过；五守卫通过；隔离浏览器中英文审计页验收通过（evidence/2026-10-12-admin-audit-changes）。

## CR-108 · 推送前的对抗式多代理审查与修复（2026-10-12，本轮新增，本地已验证）

- 方法：workflow `wf_766e2d36`（64 个只读代理）：7 个维度（授权与平台边界、数据一致性与事务、信息暴露与隐私、界面/i18n/拒绝门禁、测试诚实度、滥用与运维安全、API/SDK/事件契约）各一名审查者对 `origin/main..HEAD`（24 个提交）出具带 file:line 的发现；去重后每条由 3 名不同视角（复现、影响、设计意图）的反驳者核验，≥2 票不被反驳才成立。19 条原始发现 → 14 条成立（4 中、10 低）、5 条驳回。
- 处置（成立项全部已修，代码在本节对应提交中）：

| # | 严重度 | 维度 | 位置 | 发现 | 处置 |
|---|---|---|---|---|---|
| C1 | medium | authz | `packages/core/synwevo_core/platform_access.py:807` | Admin second-factor policy is satisfied by a mailed code plus the re-entered password, with nothing enrolled | 已修（子代理）：`platform_access.py` `session_strong()` 只认 `STRONG_SECOND_FACTORS={totp, recovery, user_verification}`，`email+password`、`idp:*+password` 不再算强会话；`test_platform_access.py` 新增 16 组 amr 参数化与邮件验证码+口令仍被锁的全链路测试；admin-coverage A11 口径改正 |
| C2 | medium | integrity | `packages/core/synwevo_core/identity.py:278` | throttle_state misclassifies MFA and e-mail-code backoff buckets as inactive 'other' rows with no account link | 已修：`identity.throttle_state(bucket, schedule)` 按记录方的退避表计算等待；`THROTTLE_KINDS` 携带退避表，新增 `account_second_factor`（`mfa:`）、`login-code:{uid}` 改为 `login_code_account` 并关联账户；前端标签与目录同步；`test_admin.py` 新增邮件验证码退避被列出、关联账户、可解除的测试 |
| C3 | medium | i18n-ui | `services/identity/web/src/governance/AccountDetail.tsx:301` | Access explanation shows the server's Simplified-Chinese, second-person verdict reason untranslated | 已修（子代理）：`AccountDetail.tsx` 以目录化句子渲染 `verdict.reason`（含「{app}」模板匹配），`check_access`/`workspace_access_failure` 的 9 句入目录；测试断言英文无汉字 |
| C4 | medium | contracts | `services/identity/synwevo_identity/admin_routes.py:1718` | Audit filter treats a catalogue code as a prefix, so one action's option also returns another action's rows | 已修：`/api/admin/audit` 的 `action` 精确匹配或按点边界前缀（`admin.user`/`admin.user.` 列出全部，`admin.user.revoke_session` 不含 `_sessions`）；新增测试；浏览器重验收冻结筛选只含 freeze 行 |
| C5 | low | authz | `services/identity/synwevo_identity/admin_routes.py:1656` | Platform-grant simulation reports feasible for grants the grant route always refuses | 已修：模拟在 `_assign_refusal` 之后调用 `access._precondition` 并对 `super_admin` 回答 `super_admin_acceptance_required`；`SIMULATION_REFUSALS` 增补 8 个代码的目录化句子；新增 Python 与 SSR 测试 |
| C6 | low | integrity | `services/identity/synwevo_identity/admin_routes.py:1440` | Admin external-identity unlink counts soft-deleted passkeys as a way in and can remove the last sign-in method | 已修（子代理）：`external.other_sign_in_methods` 只数 `deleted_at IS NULL AND rp_id=<当前>` 的安全密钥（平台权限持有者另要求 uv_capable，与 `Passkeys.remove` 同口径）；`test_external_identity.py` 新增软删除/他域/在用三种情形 |
| C7 | low | integrity | `services/identity/synwevo_identity/admin_routes.py:1152` | Admin finalize wraps finalize_deletions in a transaction, so the security journal records user_deleted before the deletion commits | 已修：`finalize_deletions(store, withdrawals=False)` 拆出 `record_deletion_withdrawals`；管理路由在事务提交后才写安全日志与撤回记录，`before` 在事务内读取；故障注入测试断言回滚后无重试标记、日志无 user_deleted 行 |
| C8 | low | exposure | `services/identity/synwevo_identity/registration.py:60` | Throttled registration audit stores raw addresses (and arbitrary strings) of non-users, one row per anonymous request | 已修：`auth.registration.throttled` 的 object_id 改为限流器自身的 SHA-256 摘要，诊断接口按摘要匹配；`/api/auth/email/resend` 先 `valid_email` |
| C9 | low | i18n-ui | `services/identity/web/src/governance/GovernanceTabs.tsx:67` | Second-factor policy card cannot end a grace window early while the policy stays required | 已修（子代理）：`secondFactorSaveChanges`（有在途宽限期时允许以 0 天保存）与 `secondFactorGraceSentence`（确认文案说明宽限期结束）；SSR 测试六种脏状态 |
| C10 | low | tests | `services/identity/tests/test_admin.py:1085` | Unified before/after test claims 'every' state-changing action but omits account status and settings publish, which lack `changed` | 已修：`AccountOperations`（账户状态）与 `SettingsOperations`（设置发布）审计 meta 经 `change()` 携带 `changed`；对应测试断言 |
| C11 | low | tests | `services/identity/web/tests/admin-account-detail.test.mjs:105` | English deletion-blocker sentence is asserted with a CJK enumeration comma | 已修（子代理）：列表分隔符改为目录化 `tr("、")`（en 为 ", "），测试正则加入 U+3001/U+FF0C |
| C12 | low | tests | `services/identity/web/tests/admin-registration-diagnostics.test.mjs:52` | Expected string 'Refused' is listed and then filtered out of the assertion | 已修（子代理）：测试期望改为 "Denied" 并删除过滤 |
| C13 | low | abuse | `services/identity/synwevo_identity/registration.py:60` | Anonymous callers can flood the security log with attacker-chosen content once IP-throttled | 已修：每个限流窗口只记一行（地址摘要每小时一行、来源地址每 15 分钟一行），匿名调用方无法以重复请求增长日志；与第 8 条合并测试 |
| C14 | low | contracts | `apps/docs/synwevo_docs/api/v1.py:1423` | OpenAPI WebhookEvent descriptions were not extended for the six new event families | 已修（子代理）：`api/v1.py` 的 `WebhookEvent.job/data` 描述与逐事件描述覆盖成员/邀请/模板 12 种事件（`job` 为 null、`data` 仅标识符），`openapi.yaml` 由 CLI 重新生成，契约同步测试通过 |

- 驳回项（记录理由，不改代码）：

| # | 严重度 | 维度 | 位置 | 发现 | 结论 |
|---|---|---|---|---|---|
| — | low | authz | `admin_routes.py:206` | The second-factor grace window can be renewed without limit by a session that has not met the requirement | 驳回（1/3 票成立）：The mechanics are as described: services/identity/synwevo_identity/admin_routes.py:177-212 gates POST /security/second-factor only on _admin(..., "platform.config.manage", dangerous=True) and recomputes grace_until = clo |
| — | low | authz | `external.py:450` | The administrator's external-identity unlink relies on a 'last sign-in method' count that includes removed passkeys | 驳回（0/3 票成立）：The claim's scenario rests on a misreading of Passkeys.remove: services/identity/synwevo_identity/passkeys.py:113-121 refuses with 409 last_factor whenever the account has no password and no other live passkey on the cur |
| — | medium | integrity | `registration.py:66` | Refused registration attempts now write one unbounded audit row per request, outside any transaction | 驳回（1/3 票成立）：The reviewer's facts reproduce (scratch probe on this branch: 10x202 then 30 refused /register -> 30 `auth.registration.throttled` rows, auth_attempts unchanged, 429 still answered; an injected audit-insert failure turns |
| — | low | tests | `test_admin.py:597` | Device-revoke test does not pin which session was ended | 驳回（1/3 票成立）：The claim isolates line 597 and ignores line 598, which is what pins the target. I replayed lines 588-598 of services/identity/tests/test_admin.py under patched `Sessions.revoke_device` regressions (scratchpad probe, fix |
| — | low | contracts | `api_platform.py:74` | `trace_id` filter on /calls has no index while the analogous request_id lookup does | 驳回（0/3 票成立）：The factual half of the claim holds (packages/core/synwevo_core/api_platform.py:64-67 creates indexes on at, (workspace_id,at) and request_id only; line 74 adds trace_id via the generic ALTER loop at store.py:934 with no |

- 审查者确认稳妥的部分（摘要）：14 条新 `/api/admin` 路由全部在权限矩阵且危险写入先做严格重认证；全部新写入在 `_write()/store.tx()` 内与审计同事务；限流列表不含 IP 与原始桶名；成员/模板事件负载与 SDK 类型逐字段一致且只含标识符；新增测试断言拒绝路径而非只有成功路径。
- 修复分工：三个子代理（前端 C3/C9/C11/C12；契约 C14；核心 C1/C6）在互不相交的文件上并行，协调者处理 admin 路由、限流分类、注册审计、终结删除事务与统一前后值覆盖；提交由协调者按明确路径一次完成。
- 验证：见 EVIDENCE 同名节（六个前端工作区 613 项、account-web 178 项含新增 SSR 测试、五守卫、infra unittest、SDK 测试、三个 Python 套件、隔离浏览器重验收）。

## CR-109 · G26 WP1 一个审计写入口、按身份描述符归因（2026-10-12，本轮新增，本地已验证）

- 位置：packages/core/synwevo_core/authority.py（`audit_actor(desc)`：user→(user, user_id)；delegated→(user, user_id)+`meta.delegation={kind: oauth_client, client_id, grant}`；service_account/api_key→(machine, `service:…`/`api:…`)；operator/无→(system, 自报标签或 operator)）；packages/core/synwevo_core/context.py（`_principal` 上下文变量与 `principal/set_principal/reset_principal`）；packages/core/synwevo_core/auth.py（`audit_event` 成为唯一写入口：显式 actor_kind/actor_id > `actor` 描述符 > `context.principal()` > system；仅给 actor_id 视为人；request_id 取请求 > 显式参数 > `context.request_id()`；meta 以规范 JSON 存储）；packages/core/synwevo_core/http.py（网关在设置 `_actor` 处同时设置/复位 principal，匿名请求为 None；`Platform.audit` 保留签名与 `workspace_given` 语义、新增 `actor=`，INSERT 委托给 `audit_event`）；packages/core/synwevo_core/capabilities.py（`_audit` 经 `audit_event` 写行，`store.execute` 加入同一事务；本包仍记 actor_kind=user，机器身份归 WP8）；tests/integration/test_audit_actor_mapping.py（新增 3 例）。
- 审查要点：账户服务 50 余处 `Platform.audit` 调用全部显式给 actor_id（默认 actor_kind=user）→ 行内容不变，仅 meta 序列化改为规范形（键排序、无空格），任何读取方都用 JSON 解析；Suite 原本就用规范 JSON；审计失败仍抛出，事务回滚语义不变（新增事务共享测试）；`describe_request` 对匿名请求不再误标为 operator（网关只在有用户或主体时设置）。
- 风险/未做：Docs 的约 80 条无审计路由（WP2–WP7）、Suite 旁路（WP9）、机器 actor_kind 与拒绝/失败/完成行（WP8）、Suite webhook 与投递列（WP10）、审计页目录（WP11）按 D7 分包推进。
- 验证：新增 3 例 + 账户服务/集成/Docs/Suite 所有读 `audit_events` 的测试文件 527 通过；五守卫通过；全量 pytest 见 EVIDENCE 同名节。

## CR-110 · G26 WP2 Docs webhook 的每个操作在事务内审计（2026-10-12，本轮新增，本地已验证）

- 位置：apps/docs/synwevo_docs/webhooks.py（`Subscriptions._audit`：`docs.webhook.create|update|pause|resume|rotate_secret|ping|redeliver|delete`，meta 只有目标主机、事件、版本号、投递/订阅标识与 `change()` 的前后值，从不含地址路径与密钥；`delete/pause/ping/redeliver` 新增 `by` 参数；`rotate_secret` 与 `ping` 的写入补上事务；`WebhookDispatcher.disable_subscription` 在同事务写 `docs.webhook.disable` 系统行，操作者 `webhook-dispatcher`）；api/v1_webhooks.py（四条路由把调用方描述符传入）；cli_webhooks.py（`_by()`：操作者描述符带 `cli:<os user>` 标签，D7 约定）；apps/docs/tests/test_webhook_audit.py（新增 3 例）。
- 审查要点：`update` 只在 UPDATE 命中时审计（版本冲突不留行）；`redeliver` 的审计在路由事务与 `Idempotency.run` 内，重放命中缓存不再进入 `work()`，测试断言同键两次请求只有一行；`ping` 的投递行与审计先提交再发网络请求，不在写锁内发送；`by` 为空时由 `audit_event` 回落到网关记录的主体，所以 CLI 之外漏传描述符的调用方仍被正确归因；审计失败使暂停/删除整体回滚（测试经 `auth.audit_event` 注入）。
- 风险/未做：`created_by` 列现在可能带 `label` 键（CLI），`authority.loads` 只按 kind 判断，不受影响；投递尝试的签名密钥 ID/时间戳列（WP10）未做；审计页对 `docs.*` 动作仍显示原始代码（WP11）。
- 验证：webhook 四个测试文件 + 契约 + CLI 共 61 通过；五守卫通过；Docs 全套件见 EVIDENCE。

## CR-111 · G26 WP3 共享模板、评论与复制申请的审计进入写入事务（2026-10-12，本轮新增，本地已验证）

- 位置：apps/docs/synwevo_docs/org_templates.py（`_audit`：`docs.template.publish|release|deprecate|restore|use_shared` 在 `_publish`/`_update` 事务内写，`set_deprecated` 与 `use` 的多条写入补上事务并在其中审计；扫描网络调用仍在事务外）；apps/docs/synwevo_docs/comments.py（`_audit`：`docs.comment.create|edit|delete|resolve|reopen`，edit/delete/resolve 补上事务；meta 只有项目/父评论标识与计数，不含正文）；apps/docs/synwevo_docs/api/app.py（删除 5 处路由层事后审计——发布/更新发布/弃用恢复/使用/删除评论；复制申请、自批与撤回的路由审计并入 `store.tx()`）；apps/docs/synwevo_docs/api/v1_copies.py（申请与撤回的审计并入事务，无 Idempotency-Key 时同样原子）；tests/integration/test_audit_sharing_templates.py、test_audit_sharing_comments.py（新增 4 例）。
- 审查要点：浏览器路由与 v1 机器路由共用同一服务对象，审计搬进服务后两面各产生恰好一行（原先 v1 的组织模板路由无审计、浏览器路由事后审计）；操作者取 `context.principal()`，机器委托调用因此带上 `meta.delegation`，服务被直接调用时回落到调用方给的 uid；复制决定保留在路由层（账户服务审批路由已有 `organization.copy.decide` 行，搬进共享类会双行）；评论删除的管理员代删标 `by_administrator`。
- 风险/未做：`use` 绑定项目时的 `update_project` 写入由 WP4 审计；复制申请在 Docs 路由层而非 `ProjectCopies` 内审计，账户服务审批路由的 `decide` 行格式与 Docs 自批行不同（WP11 目录时统一标签）。
- 验证：既有评论/组织模板/复制/事件/契约测试 99 通过；新增 4 例 + webhook 审计 3 例通过；五守卫通过；Docs 与集成全套件见 EVIDENCE。

## CR-112 · G26 WP4 Docs 项目与来源的写操作在服务层事务内审计（2026-10-12，本轮新增，本地已验证）

- 位置：apps/docs/synwevo_docs/services.py（新增 `App.audit(action, object_kind, object_id, **meta)`：经 `auth.audit_event` 写 `docs.<action>`，操作者取网关放入上下文的请求主体，直接调用方回落为 system；`create_project`/`delete_project`/`remove_source`/`reorder_sources`/`set_representative` 补上事务，`update_project` 整体进入一个事务（原浏览器路由在自动提交下读修订号再写，两个标签页可同时通过陈旧检查），`add_source` 的插入、顺序、代表文件与审计同事务、解析/扫描经 `after_commit` 在提交后启动，`set_source_options` 在既有事务内审计，`desensitize_sources` 批次写一行汇总；`_project_changed` 改经 `store.after_commit`——回滚的更改不再进入活动流、钩子不再在写锁内运行）；apps/docs/synwevo_docs/views.py（`_run_ocr` 在既有事务内审计并把 `bump_project` 收进事务；`set_correction` 补上事务并审计）；tests/integration/test_audit_coverage_projects.py（4 例）、apps/docs/tests/test_audit_projects_v1.py（4 例）。
- 动作与 meta（只有标识符与计数）：`docs.project.create {origin}`、`docs.project.update {fields, before/after/changed 仅覆盖 template_id/template_version/archived_at/settings_rev}`、`docs.project.delete`、`docs.source.add {project_id,size,ord,representative,scan}`、`docs.source.remove`/`representative {前后代表文件}`、`docs.source.reorder {前后顺序 id 列表}`、`docs.source.options {keys, 版本前后}`、`docs.source.desensitize {requested,produced,skipped,added_to_project,sources}`、`docs.source.ocr {pages,pages_total, 状态与版本前后}`、`docs.source.correction {mapping_id,cleared,confirmed, 版本前后}`；项目名、文件名、规则与输出文本、更正内容、识别文字、blob 摘要一律不入。
- 审查要点与偏离：只改 `step` 的 PATCH 是工作台导航，不写行（计划把 step 列为标识符键，这里按用途收窄：与其他字段同时变化时仍出现在 `fields`）；`definitions.import_project` 在一个事务里最多调三次 `update_project`，各自一行、同一 request_id（WP5 再加 `docs.project.import` 汇总行）；脱敏批次按既有"逐文件部分成功"契约不可整体回滚，汇总行描述结果而非守护它，加入项目的每个副本另有自己的 `source.add` 行；`retry_source_scan` 原有 `docs.source.scan.retry` 行不变。
- 验证：浏览器 11 条写路由各恰好一行（`actor=user`、空间、对象、request_id、`changed` ⊆ 标识符键；步骤导航零行；审计失败回滚改名与 settings_rev 且无活动行；查看者 403 零行）；v1 机器调用 10 条路由各恰好一行（`actor_kind=machine`、`actor_id=api:<key id>`；Idempotency-Key 重放不加行；陈旧修订 409 无行；OCR 行无识别文字）；既有项目/来源/协作/网关/复制测试 135 通过；五守卫 + 图标检查通过；完整根套件在 WP5 后、推送前一并跑。
