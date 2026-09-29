# Synwevo Admin 批次 1：官方产品行为调研

> 批次 1 设计依据。由调研代理于 2026-09-29 阅读各产品官方文档整理；每条注明来源与页面/访问日期。
> 标注为「Synwevo 自定」的是本项目的决定，不是文档所述。实现与验收以 admin-coverage.md 为准。

访问日期：2026-09-29。页面日期取自页面本身；页面上没有日期的，只写"访问"。下文只用实际读到的官方内容，引用都是短语，其余为转述。不猜各产品的内部实现。

## (a) 分级平台管理员角色，还是一个超级管理员标志

**a1 角色是权限的组合**
- 问题：只有 `is_super_admin` 一个标志时，客服为了重置 MFA 也得拿到全部权限。
- 参考：
  - Google Workspace 有 13 个预置管理员角色，例如 User Management Admin、Help Desk Admin、Services Admin、Multi-party Approval Admin。其中部分角色可以限定到组织单元。创建角色、修改管理员账号只有 Super Admin 能做。
  - 另外最多可建 750 个自定义角色，每个由若干 privilege 组成，而且只有超级管理员能建。
  - 来源：[预置角色](https://knowledge.workspace.google.com/admin/users/prebuilt-administrator-roles)、[自定义角色](https://knowledge.workspace.google.com/admin/users/create-edit-and-delete-custom-admin-roles)，页面日期 2026-09-24。
- 理由：三个应用加账户服务，运维职责天然就是分开的。
- 建议：
  - 先建权限目录（点分字符串），角色就是一组权限。
  - 内置角色：平台超级管理员、身份管理员、支持（帮助台）、API 平台管理员、审计查看员、只读管理员。
  - `api_platform.manage` 改为授予某个角色的权限，不再挂在标志上。
  - 任何管理员角色都不包含业务文档读取权。
- 验收：做一张"角色 × 操作"的允许/拒绝矩阵表驱动测试，覆盖全部内置角色。

**a2 按权限、范围、时长三个维度授权；低权限不能动高权限的人**
- 参考：
  - Microsoft Entra 的最小权限原则讲三件事：一组特定权限、一个特定范围、一段特定时间。Entra 有 65 个以上内置角色，另有自定义角色和管理单元。来源：[Entra 最佳实践](https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/best-practices)，页面日期 2026-06-01。
  - Microsoft 365 管理中心：Helpdesk 管理员只能处理非管理员用户。例如只需重置密码时，给 Password Administrator，不给 Global Administrator。来源：[M365 管理员角色](https://learn.microsoft.com/en-us/microsoft-365/admin/add-users/about-admin-roles)，页面日期 2026-06-18。
- 建议：
  - 角色分配带三个字段：`scope`（platform / app:docs / app:messaging）、可选的 `expires_at`、授予理由。
  - 服务层规则：目标用户持有的权限，如果操作者自己没有，就拒绝操作。
- 验收：支持角色给普通用户重置 MFA 返回 200，给管理员重置返回 403 并写入审计。过期的分配立即失效。

**a3 成员管理权和"授予最高权"分开**
- 参考：
  - Cloudflare：Administrator 不能管理成员，也不能管理账单，这两项只有 Super Administrator 能做。另有 Administrator Read Only 和 Audit Logs Viewer 等角色。策略由 actor、role、scope 三部分组成。来源：[Cloudflare 角色](https://developers.cloudflare.com/fundamentals/manage-members/roles/)，页面日期 2026-09-28。
  - Stripe：IAM Administrator 可以邀请和移除成员，但不能授予 Administrator 或 Super Administrator。原文："Only a Super Administrator can assign the Super Administrator role"。多角色时权限取并集，文档提醒注意由此产生的意外授权。来源：[Stripe 角色](https://docs.stripe.com/get-started/account/teams/roles)，页面无日期，访问 2026-09-29。
- 建议：
  - `admin.roles.assign` 和 `admin.roles.assign_super` 拆成两个权限。
  - 权限计算取并集，界面上展示"有效权限"。
- 验收：IAM 类角色授予超级管理员角色时返回 403。多角色用户的有效权限等于各角色权限的并集，用快照测试固定。

## (b) 危险操作前的 step-up / 近期重新认证

**b1 限时的 sudo 窗口**
- 参考：GitHub sudo mode。
  - 适用操作：加 SSH key、生成 PAT、管理 webhook、改组织成员与安全设置、查看恢复码等。
  - 时长原文："two-hour session timeout period"。窗口内每做一次敏感操作，计时器重置。
  - 接受的方式：密码、passkey、安全密钥、GitHub Mobile、TOTP。不接受短信。
  - 来源：[GitHub sudo mode](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/sudo-mode)，页面无日期，访问 2026-09-29。
- 理由：平台管理员的影响面比单个用户大得多。
- 建议（以下为 Synwevo 自己的决定，不是来自文档）：
  - 会话记录最近一次强认证的时间和方式。
  - 操作类型上声明 `step_up: required`，由服务端检查 `now - last_strong_auth ≤ 15 分钟`。窗口不因后续操作续期，比 GitHub 更严。
  - 只接受 passkey 和 TOTP。
  - 不满足时返回结构化错误 `step_up_required`，前端完成重新认证后用同一个 idempotency key 重试。
- 验收：
  - 冻结时钟测边界：第 14 分钟通过，第 16 分钟拒绝。
  - 被拒请求在审计里记为 `denied/step_up_required`。
  - 短信和邮件验证码不能用于 step-up。

**b2 按需提权（just-in-time）**
- 参考：Entra PIM。
  - 可以为激活角色强制 MFA、要求填写理由、需要审批、限定时长，激活时发通知，到期自动收回。
  - 历史审计可下载。
  - 来源：[PIM 概览](https://learn.microsoft.com/en-us/entra/id-governance/privileged-identity-management/pim-configure)，页面日期 2026-04-23。
- 建议：
  - 本批先不做审批流。数据模型先预留"可激活（eligible）/ 已激活（active）"两种分配状态。
  - 超级管理员角色后续可以改为"激活 1 小时 + 填写理由"。
- 验收：（后续批次）激活后超时，下一次请求返回 403。

**b3 生成密钥类操作需要额外验证**
- 参考：Stripe。
  - 创建 secret key 时要输入通过邮件或短信收到的验证码。
  - 创建、修改 access policy 时，按提示完成认证（"If prompted to authenticate"）。
  - 文档推荐 passkey 或安全密钥；短信只作最后手段。
  - 来源：[Stripe API keys](https://docs.stripe.com/keys)、[Security](https://docs.stripe.com/security)，访问 2026-09-29。
- 建议：以下操作一律标为 step-up：服务凭据的创建和轮换、秘密的展示、管理员角色变更。秘密只在创建或轮换时展示一次，与仓库规则一致。
- 验收：API 测试确认，没有完成 step-up 就拿不到秘密明文。

## (c) 保护最后一个超级管理员，以及管理员交接

**c1 至少保留一个，建议至少两个**
- 参考：Google Workspace。
  - 账号里至少要有一个超级管理员，建议至少两个。原文："more than one super administrator account, each managed by a separate individual"。
  - 超级管理员另设一个日常账号；用安全密钥；准备备用密钥和备份码。
  - 来源：[管理员账号安全实践](https://knowledge.workspace.google.com/admin/users/security-best-practices-for-administrator-accounts)，页面日期 2026-09-24。
- 建议：
  - 在服务层的单个事务里检查一条不变量：有效超级管理员（活跃、已注册 MFA、未锁定）的人数必须 ≥ 1。
  - 降级、停用、删除、移除 MFA 这几类操作都要过这条检查。
  - 只剩 1 个时，概览页常驻提示"建议 ≥ 2"。
- 验收：
  - 对最后一个超级管理员做上述任一操作，都返回 409 `last_super_admin`。
  - 并发测试：两人同时互相降级，只能成功一个。

**c2 禁止自降；数量上限与应急账号**
- 参考：
  - Microsoft 365：Global Administrator 不能移除自己的 Global Administrator 分配，目的是避免出现零个全局管理员。来源：[M365 角色](https://learn.microsoft.com/en-us/microsoft-365/admin/add-users/about-admin-roles)。
  - PIM："Prevent removal of the last active Global Administrator"，Privileged Role Administrator 同样适用。来源：[PIM 概览](https://learn.microsoft.com/en-us/entra/id-governance/privileged-identity-management/pim-configure)。
  - Entra 建议全局管理员 "fewer than five"；达到 5 个时，概览页出现告警卡片。来源：[Entra 最佳实践](https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/best-practices)。
  - 应急账号：至少 2 个；认证用抗钓鱼方式，并且和日常方式不同；每次使用都告警；至少每 90 天演练一次。文档还提醒一种锁死情形：所有角色都需审批，但没有任何审批人。来源：[应急访问账号](https://learn.microsoft.com/en-us/entra/identity/role-based-access-control/security-emergency-access)，页面日期 2026-06-04。
- 建议：
  - 禁止自降级。超级管理员超过 5 个时显示告警。
  - 应急路径用主机上的 `scripts/platform.py`（需要主机权限）。使用时写审计、通知其他管理员，并纳入 90 天演练。
- 验收：自降级返回 403。应急命令在测试实例上执行后，会留下审计记录和通知。

**c3 交接：先加人，再退出**
- 参考：
  - GitHub 建议 "at least two people within each organization have the owner role"。owner 不能改自己的角色。转移所有权时，要先把 owner 角色交给别人，再移除自己。来源：[所有权连续性](https://docs.github.com/en/organizations/managing-peoples-access-to-your-organization-with-roles/maintaining-ownership-continuity-for-your-organization)、[转移所有权](https://docs.github.com/en/organizations/managing-organization-settings/transferring-organization-ownership)，访问 2026-09-29。
  - Stripe：Account Owner 只有一个，只能由 Owner 本人转移。来源：[Stripe 角色](https://docs.stripe.com/get-started/account/teams/roles)。
  - Cloudflare：超级管理员要离开时，可以先邀请一位新的超级管理员。来源：[成员管理](https://developers.cloudflare.com/fundamentals/manage-members/manage/)，页面日期 2026-04-20。
- 建议：交接分三步：授予 → 新管理员登录并完成 step-up（视为接受）→ 旧管理员退出。
- 验收：新管理员接受前，旧管理员不能退出。三步各写一条审计。

## (d) 类型化的管理操作与审计日志

**d1 字段参照**
- 参考：
  - Google 管理员日志：Actor、Event、Date、IP、Old/New value、Resource、Target、组织单元，筛选条件可用 AND/OR 组合。来源：[Admin log events](https://knowledge.workspace.google.com/admin/reports/admin-log-events)，页面日期 2026-09-24。
  - Entra：列表显示时间、服务、类别、活动、状态；详情里有 correlation ID、actor/target、新旧值。原文说条目 "system generated and can't be changed or deleted"。来源：[Entra 审计日志](https://learn.microsoft.com/en-us/entra/identity/monitoring-health/concept-audit-logs)，页面日期 2026-06-05。
  - GitHub 企业审计日志的通用字段包括 `@timestamp, action, actor, actor_id, operation_type, request_id, user, user_agent, token_id, programmatic_access_type`。operation 有七种：create、access、modify、remove、authentication、transfer、restore。组织审计日志保留 180 天，可导出 JSON/CSV。来源：[企业审计事件](https://docs.github.com/en/enterprise-cloud@latest/admin/monitoring-activity-in-your-enterprise/reviewing-audit-logs-for-your-enterprise/audit-log-events-for-your-enterprise)、[组织审计日志](https://docs.github.com/en/organizations/keeping-your-organization-secure/managing-security-settings-for-your-organization/reviewing-the-audit-log-for-your-organization)，访问 2026-09-29。
- 理由：这三家的字段组合，正好覆盖我们需要的 actor、target、前后值、结果、request id。

**d2 失败也记录，并区分入口**
- 参考：
  - Cloudflare Audit Logs v2 有 ActionResult，失败的尝试也记录。其他字段：ActorType/Context/TokenDetails、Resource type/scope/product、Raw（HTTP 方法、状态码、URI、Ray ID），还标明操作来自 dashboard 还是 API。保留 18 个月。来源：[Audit Logs v2](https://developers.cloudflare.com/fundamentals/account/account-security/audit-logs/)、[字段表](https://developers.cloudflare.com/logs/logpush/logpush-job/datasets/account/audit_logs_v2/)，页面日期 2026-09-14。
  - Stripe 的 security history 可按日期和操作筛选，并导出 CSV。来源：[组织成员管理](https://docs.stripe.com/get-started/account/orgs/team)。
- 建议：
  - 建一个操作注册表。每个操作声明：名称、operation_type、所需权限、是否 step-up、危险等级、目标类型、输入 schema，以及带脱敏的 before/after 投影。
  - 唯一执行器按顺序走：authz → step-up → 不变量检查 → 执行 → 在同一事务里写审计。被拒绝和执行失败的情况走独立写入，不会随业务事务一起回滚。
  - 审计记录字段：actor（human/service/cli）、interface（console/CLI/SDK）、action、target、scope、before/after、result（succeeded/denied/failed + 原因码）、`request_id`（等于响应头里的值）、ip、user_agent、理由。
  - 日志只追加：不提供修改和删除接口。
  - CLI、SDK、控制台共用这一个执行器。
- 验收：
  - 每个注册操作都有测试，断言恰好产生一条审计，且前后值正确。
  - 权限拒绝会产生一条 `denied` 记录。
  - 用秘密模式做 grep 测试，before/after 里不出现秘密。
  - 响应头的 `request_id` 能在审计里查到对应记录。

## (e) 管理台外壳：导航分组、范围/环境指示、全局搜索

**e1 按对象领域分组导航**
- 参考：
  - M365 管理中心左侧导航：Home、Users、Teams and groups、Roles、Billing、Support、Settings、Setup、Reports、Health、Admin centers。各服务的深度设置放在独立的专项管理中心。导航有 "Show all"，也有搜索栏。来源：[M365 管理中心概览](https://learn.microsoft.com/en-us/microsoft-365/admin/admin-overview/admin-center-overview)，页面日期 2026-02-02。
- 建议：
  - 分组为：概览 / 身份与账户 / 应用运维（Docs、Messaging，不含业务内容）/ API 平台 / 安全与审计 / 平台设置 / 运行状况。
  - 导航根据服务端下发的能力列表渲染。隐藏的入口在服务端同样拒绝访问。
- 验收：
  - 对每个角色的导航做快照测试。
  - 直接访问被隐藏的路由返回 403。

**e2 全局搜索按权限过滤**
- 参考：Google 管理控制台可以搜索用户、群组、功能、设置、设备、会议，结果上方有筛选 chip。原文："Your Admin console privileges determine what results are shown."。来源：[控制台搜索](https://knowledge.workspace.google.com/admin/getting-started/search-in-the-admin-console)，页面日期 2026-09-24。
- 建议：
  - 可搜索：用户、组织、空间、管理功能页、按 request_id 查审计。
  - 在服务端按调用者权限过滤结果。
  - 永远不返回业务文档的标题或内容。
- 验收：审计查看员搜不到用户管理设置。测试数据里的文档标题不出现在任何搜索结果中。

**e3 环境与范围一直可见**
- 参考：
  - Stripe 通过账户选择器进入 sandbox。sandbox 与 live 数据隔离，可以只邀请用户进 sandbox。来源：[Sandboxes](https://docs.stripe.com/sandboxes)，访问 2026-09-29。
  - Carbon 的 header 工具区是 "reserved for universal, system-level functions such as profile, search, notifications"，从左到右依次为搜索、帮助、通知、账户、切换器。来源：Carbon 官方站点源文件 [UI shell header](https://raw.githubusercontent.com/carbon-design-system/carbon-website/main/src/pages/components/UI-shell-header/usage.mdx)，访问 2026-09-29。
- 建议：
  - header 常驻两样东西：
    - 环境徽标：取自服务端 `/healthz` 返回的 instance，颜色只用共享令牌。
    - 当前范围：平台，或某个组织。
- 验收：测试实例显示"测试"徽标。徽标由服务端值决定，前端无法伪造。

## (f) 危险操作的确认：输入名称确认与影响预览

**f1 多步确认 + 输入目标名称**
- 参考：GitHub 删除仓库。
  - 入口在 Danger Zone。依次点击 "I want to delete this repository"，阅读警告后勾选 "I have read and understand these effects"，再输入仓库名，最后删除。
  - 警告会说明哪些后果是永久的，以及对 fork 的影响。部分仓库在 90 天内可以恢复。
  - 来源：[删除仓库](https://docs.github.com/en/repositories/creating-and-managing-repositories/deleting-a-repository)，访问 2026-09-29。
- 建议（按危险等级分四级）：
  - L1 可撤销：不弹确认，提供撤销。
  - L2：危险对话框。
  - L3 不可逆或影响面大：服务端算出影响预览，要求输入目标的准确标识（邮箱或组织 slug），并完成 step-up。
  - L4：授予超级管理员等操作，后续批次加双人审批。
  - 能软删除的操作就提供恢复期。

**f2 危险对话框的写法**
- 参考：
  - Carbon 的 danger modal 用于破坏性或不可逆操作：主按钮换成 danger 按钮，标题和按钮都写明具体动作，并且 "Clearly describe the action being confirmed and explain any potential consequences"。
  - 批量操作：选中条目后，表格顶部出现批量操作栏，行内操作同时禁用；点取消或全部取消选择即退出。
  - 来源：Carbon 官方站点源文件 [Modal](https://raw.githubusercontent.com/carbon-design-system/carbon-website/main/src/pages/components/modal/usage.mdx)、[Data table](https://raw.githubusercontent.com/carbon-design-system/carbon-website/main/src/pages/components/data-table/usage.mdx)。这两个页面的渲染版抓取被截断。
- 建议：
  - 按钮写动词，例如"停用 3 个用户"，不写"确定"。
  - 批量危险操作在按钮上显示选中数量。
- 验收：
  - 输入值完全匹配之前，danger 按钮保持禁用。
  - 行内操作在批量模式下不可用。

**f3 执行前先看影响摘要；敏感操作可设第二人审批**
- 参考：
  - Cloudflare 编辑成员时，要先点 "Continue to summary" 核对，再点 Update。来源：[成员管理](https://developers.cloudflare.com/fundamentals/manage-members/manage/)。
  - Stripe 删除 access policy 前，确认对话框会说明影响。给 agent 用的 key 默认受审批规则约束，付款、退款、配置变更等要由指定审核人批准。来源：[Stripe API keys](https://docs.stripe.com/keys)。
  - Google 多方审批：敏感设置的修改需要另一位管理员批准。来源：[Multi-party approval](https://knowledge.workspace.google.com/admin/security/multi-party-approval-for-sensitive-actions)，页面日期 2026-09-24。
- 建议（以下为 Synwevo 设计，非文档所述）：
  - 预览接口返回影响数量（撤销多少会话、作废多少凭据、涉及多少空间）和一个 `preview_token`。token 绑定"操作 + 目标 + 影响摘要哈希"，短时有效。
  - 执行时必须带上 token。状态在预览后发生变化的，返回 409，要求重新预览。
- 验收：
  - 不带 token 返回 400；输入名称不符返回 400；token 过期返回 409。
  - 预览里的数量与执行后审计记录的实际影响数量一致。

## 没能读到或页面没写的内容
- Carbon 的 modal 和 data table 渲染页抓取被截断，改读 carbon-design-system 官方仓库里的站点源文件；dialog pattern 源文件返回 404。
- Fluent 没有查。
- Stripe 没有找到一个通用的"重新认证窗口"说明，只读到创建 secret key 要输验证码、改 access policy 时"按提示认证"。
- Cloudflare 审计日志 v1 页面没有列字段，字段取自 v2 的页面。
- Google 多方审批页面没有说明请求何时过期，也没有说申请人能否自批。
- Google 自定义角色页面没有分别说明控制台权限和 API 权限。
- GitHub 组织页面没有写明最后一个 owner 能否被移除。
