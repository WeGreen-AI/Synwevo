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
