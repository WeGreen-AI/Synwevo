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
| CR-045 | Docs 的项目复制与组织模板库是独立的跨空间路径，不读取数据流政策（来源设为 `none` 仍可经这两条路径带出）| 低：与“唯一契约”的表述不符 | 原有 | 本批只在 `docs/platform/data-flow.md` §5 如实说明；纳入契约另列 BACKLOG | 待处理（未实现）|
| CR-046 | Admin 运行页独立审查 AR-1–AR-10：`check_brand` 因未加引号的 `docweave` 键失败（CI 门槛）；`blocked/unknown/paused` 流程对“需要关注”、汇总与筛选不可见；调用方自定 `correlation_id` 与作者自定 FailStep code 作为自由文本进入页面；列表做全表工作；首次加载焦点被标题抢走；两处英文标签错误等 | 高/中/低 | 并行会话未提交实现（admin-governance）| 工作流 wf_653d06ea-6cf 逐项修复并由两路只读核查复验 | 修复中 |
| CR-047 | 对 a407c2c 的三路对抗复核（wf_5138fc3a-478，18 项，17 项有通过的复现脚本，scratchpad/df-skeptics.json）：**高** F1 流程谱系只跟随 `$ref`，字面量重读中转空间即可洗掉来源；**高** F2/AGENT-DF-1 `audience` 放行对已向外部暴露记录的普通写入（填写页返回表单行的实时标题/说明，`forms.form.publish` 声明为 write）；**中** F3 事件触发串联两个流程跨运行带出、F4/AGENT-DF-2 分支与等待谓词读取步骤值不经转移判定（`none` 下亦然，Agent 还能从 `agent.flow.run` 的步骤路径读出）、AGENT-DF-3 会话内复制后来源丢失、DFP-1 默认组织政策/上限下对外发送可把组织内容带出组织；**低** F5–F7、AGENT-DF-4/5、DFP-2–6（含文档不实与 Automations 页文案） | 高：已推送到 feat/business-apps（草稿 PR，未合并 main、未部署）的 data_flow 仍可被流程/Agent 自动化绕过 | 本轮 data_flow（a407c2c）| 三个隔离 worktree 并行修复（wf_3435d8f7-ca4）：流程改为“运行级来源”保守规则（凡本次运行及其事件链读过的空间都算来源，任何有副作用的写入逐一判定）；填写页只提供已发布版本，新增“对外可见写入”标记，组织范围下对外写入拒绝；Agent 记住会话内写入带入的来源、对模型只返回流程运行状态、确认依据变化时重发卡片 | F1、F3–F7 已修复 43e7e3c（df-flows，协调者复核 diff 后快进合入）；F2/AGENT-DF-1 已修复 0f0124b（填写页与提交人通知只用所答版本；公开链接跟随最新版本时发布按对外发送判定 `delegation.transfer_from`）；DFP-1 已修复 af62448（组织范围内容不能经对外发送离开组织，规则 9 `transfer_organization_external`）；AGENT-DF-2 Agent 半边已修复 64cdf9d（流程运行进度按运行来源计入会话来源）；AGENT-DF-3 已修复 f7afc66（会话内写入带入的来源在转移判定中保留，处理政策仍随目标）；DFP-2（组织冻结/删除后任何应用操作含政策修改均拒绝）、AGENT-DF-5（会话范围约束 Agent 定义/启动的流程）、AGENT-DF-4（确认依据变化时重发卡片而非永久 409）、DFP-5 与 CR-048 DF-B1 前端部分（编辑器不再在客户端拒绝跨空间引用、不再提供个人操作为步骤、`transfer_*`/`person_required` 显示三语原因）本批修复；DFP-3/DFP-4 文档已改。每项均有变异验证（全部被测试捕获，DFP-1 一项为等价变异）。DFP-6 已修复 9048d40。第二轮对抗复核（scratchpad/df-round2）：无高；中 R2-F1（固定版本的公开链接可在提交时指名新版本读到其规则提示）、R2-F2（Agent 写入触发的流程链丢失 Agent 记录的来源）、R2-F3（会话兄弟分支丢失带入来源）、R2-F4（carried 按运行重复扫描整条链且调用方可自选链）；低 R2-F5–F8 —— 全部在本批修复，11 项变异全部被捕获，apps/suite+integration 1857 通过。剩余：CR-048 其余 |
| CR-048 | 对 a407c2c 的正确性审查（只读子代理，scratchpad/agents/dataflow-correctness/t/ 含复现测试；无阻断/高）：**中** DF-B1 自动化编辑器与服务端契约矛盾（仍可选个人操作为步骤、仍在客户端拒绝一切跨空间引用、`transfer_*`/`person_required` 无专门文案）；DF-B2 拒绝发生在派发之后（result_read 的检查在 `_complete_call`）时尝试次数已计入，政策修正后 `resume` 答 409 `flow_attempts_exhausted`；DF-B3 步骤等待异步回执期间遇到拒绝会丢失 `operation_wait` 标记，之后永远 `flow_reconciliation_required`（第三方改政策即可触发）；DF-B4 规格里不存在的 step_id 或无法再校验的已存规格使 `StopIteration`/`ValidationError` 逃出 `tick()`，`_wake_waits` 因此每次 tick 都失败、所有用户的运行都不再被认领（需损坏的行或跨版本回滚才可达）。**低** DF-B5（同 AGENT-DF-4）、DF-B6 拒绝未指明是哪个来源、DF-B7 八处保护没有会因其失效而失败的测试、DF-B8 文档/注释三处不实 | 中：流程可恢复性与引擎健壮性；不构成越权 | DF-B2/B3/B4 的 `_failure`/`_spec`/`_wake_waits` 为原有（WP7/WP8），其余为本轮 data_flow | DF-B1 余项、B2、B3、B4、B6、B7、B8 在第二轮三支修复合并后作为后续批次处理（引擎文件正由 df-flows 改写，避免并行冲突）；DF-B5 与 AGENT-DF-4 合并处理 | 待处理（排在 wf_3435d8f7-ca4 合并之后） |
