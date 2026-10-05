# 付费模型调用的风险控制：单次上限、自动刹车、总闸、放行名单、上游余额与对账

适用范围：经平台账户（受治理的 DeepSeek 路径，`MeteredDeepSeek`）发出、由用户付费的模型调用。个人自带密钥不经过这里。
代码：`packages/core/synwevo_core/billing/risk.py`（刹车、总闸、放行名单）、`upstream.py`（余额读取与对账）、
`risk_monitor.py`（定时任务）、`admission.py`（准入中调用）、`cost_settings.py`（策略字段）；管理接口
`services/identity/synwevo_identity/admin_model_risk.py`，管理页 Account `/admin?tab=model-risk`。
本文说明已实现的行为与边界，不宣称任何吞吐量或真实上游已验证；生产参数、两个停止开关与告警处理见
[operations.md §11](../operations.md)（起点参数由 `tests/integration/test_model_risk_guidance.py` 按 §11.2 的规则逐条核对）。

**两个停止开关**（详见 operations.md §11.1）：一级是发布 `enabled=false` 的成本策略版本，下一次准入即拒绝（策略在发送事务里读取，
不需要重启），已发送调用、恢复、监控与对账照常；二级是从 `SYNWEVO_SUITE_APPS` 去掉 `agent` 并重建 portal，Agent、计量恢复与
`RiskMonitor` 都不再安装（`apps/suite/tests/test_agent_risk_controls.py::test_second_kill_switch_removing_agent_from_the_release_list_leaves_no_paid_path`），
期间没有余额读数。先用一级、确认没有进行中的调用，再考虑二级。总闸（§4）是一级的手动等价入口，不产生策略版本。

## 0. 默认状态

- 没有发布成本策略时什么都不运行：付费调用不开始（既有行为），余额监控、对账与暴露提醒也不运行，管理页如实显示“尚未发布成本策略”。
- 成本策略没有 `risk` 段时：正式环境（`SYNWEVO_BILLING_ENVIRONMENT=live`）**默认只允许放行名单中的付款方**（首次生产开放即为分阶段开放），
  测试环境不限制；没有刹车阈值、余额阈值和暴露提醒；每日账本审计照常运行。
- 没有 `max_call_cost` 时不限制单次成本（仍受路线上限、运行预算、个人周期预算与平台成本上限约束）。
- 总闸默认允许付费调用；它只因上游余额、上游 402 或管理员而关闭，只由管理员重新开启。

## 1. 在哪里拒绝

全部检查都在 Ledger 一次性发送围栏的**同一个写事务**里、任何写入之前执行（`CostAdmission.admit`）。被拒绝的调用
什么也没有发送，钱包冻结由网关释放，因此用户看到的每一种拒绝都可以如实说“尚未发送，未扣费”。顺序：

| 次序 | 检查 | 拒绝代码 |
|---|---|---|
| 1 | 成本策略启用（`enabled`） | `cost_admission_disabled` |
| 2 | 单次调用的保守成本上界 ≤ `max_call_cost` | `cost_call_limit_exceeded` |
| 3 | 平台日/周/月成本、未知用量金额、在途与未知数量（既有） | `cost_*` |
| 4 | 平台总闸未关闭 | `risk_breaker_open` |
| 5 | 放行名单模式开启时付款方在名单中 | `risk_not_allowlisted` |
| 6 | 付款方、模型路线没有生效中的暂停 | `risk_payer_suspended` / `risk_route_suspended` |
| 7 | 付款方、模型路线的滚动窗口成本加上本次上界不超过阈值 | `risk_payer_brake` / `risk_route_brake` |

`risk_accounting_capacity`：窗口内记录超过 10,000 条时拒绝（不在发送路径上读无界历史）。策略、计数或状态不可读时拒绝，不放行。
Agent 对话里这些代码带 `billing_` 前缀（网关把 BillingError 包成 `billing_<code>`）。这些代码是稳定契约，界面文字归对话界面：
`apps/suite/web/src/apps/agent/refusals.ts`（chat-ux 工作包）按代码与词干分类——`billing_cost_call_limit_exceeded` → 单次上限；
`billing_risk_*` 中含 `allowlist` → 分阶段开放、含 `breaker` → 服务暂不可用、含 `brake` → 自动暂停、含 `suspend` → 已暂停，其余
`risk_*` 与 `cost_*`（含 `cost_admission_disabled`）→ 服务暂不可用。每一句（三语言）都写明没有发起调用、没有扣费；总闸从不写成
“你的余额不足”（上游 402 是平台账户的余额，不是用户钱包）。新增风险代码时沿用 `risk_` 前缀与这些词干，不需要改界面就能正确归类。

## 2. 成本策略里的字段

在 Account 管理端「模型成本」页与其他成本上限同一版本、同一预览/理由/审计流程发布；旧版本没有这些字段时原样保留，
表单要求显式选择“启用/未设置”，不会悄悄丢掉已有阈值。金额都是**成本策略单位的最小单位整数**（提供方成本，不是售价）。

| 字段 | 含义 | 取值 |
|---|---|---|
| `max_call_cost` | 一次调用可预留的保守成本上界（路线输入上限 × 未命中价 + 输出上限 × 输出价）的最大值 | 可省略 = 不限 |
| `risk.allowlist_only` | 是否只允许放行名单中的付款方 | 布尔，必填 |
| `risk.payer_window_s` / `payer_window_cost` | 付款方滚动窗口（秒）与窗口内提供方成本阈值 | 60–86400；阈值 `null` = 不检查 |
| `risk.route_window_s` / `route_window_cost` | 模型路线（`requested_model`）的窗口与阈值 | 同上 |
| `risk.balance_warn` / `balance_close` | 上游余额预警阈值、停止阈值（同一单位；停止 ≤ 预警） | `null` = 不检查 |
| `risk.balance_poll_s` | 上游余额读取间隔 | 60–86400 秒 |
| `risk.reconcile_tolerance` | 每日对账容差 | 必填，≥ 0 |
| `risk.pending_alert` | 用量待确认的预留成本超过此值时提醒 | `null` = 不提醒 |

余额阈值与上游余额比较，因此成本单位代码必须与上游余额币种一致（CNY 价目表对应 `CNY`）；不一致时读数记为 `currency_mismatch`，不比较。

## 3. 自动刹车

- 计量的是**平台付出的提供方成本**：窗口内已结算调用取精确成本（分数），用量未知的调用取保守预留上界；按准入时刻
  `admitted_at` 落入窗口。付款方按付费钱包的所有者统计（个人或组织付款方），路线按请求的模型路线统计。
- 一次调用会使窗口合计超过阈值时：拒绝这次调用，并在拒绝事务回滚**之后**锁定暂停（`CostAdmission.after_refusal`，网关调用；
  写在拒绝事务里会随之回滚）。锁定写入 `model_risk_suspensions`、系统审计 `billing.risk.brake_triggered`、安全日志
  `model_dispatch_suspended`（先写日志再提交），并在提交后通知平台管理员与该付款调用的发起人（三语言）。
- 暂停不会随窗口过去自动解除；只有平台管理员在管理页写明理由并重新认证后解除（`admin.model_risk.suspension_lift` 审计）。
  解除后若速率仍超过阈值，下一次调用再次锁定。锁定写入失败时只记日志；拒绝由事实计算，下一次调用会再次检测到。
- 同一付款方/路线同时只有一条生效暂停（唯一索引）；暂停记录只追加，解除只能填写一次，不能删除或改写。

## 4. 平台总闸

- 每个计费环境一条事件序列（`model_risk_breaker_events`，只追加），最新一条决定状态。关闭来源：`balance`（余额低于停止阈值，
  或上游报告账户不可用）、`upstream_402`（网关收到上游 402）、`admin`（管理员手动，需理由）。
- **自动关闭只有一个入口**：`trip_platform_breaker(store, environment=..., source=..., reason_code=...)`，余额监控与网关的上游 402
  分支都调用它。它是幂等的：已关闭时返回当前事件，不重复写入、不重复通知。自动关闭写系统审计 `billing.risk.breaker_opened`
  与安全日志 `model_breaker_opened`，提交后通知管理员。网关在 402 处的调用点由网关工作包接入（上游 402 是平台账户余额问题，
  不是用户钱包问题）。
- 只有管理员重新开启（理由 + 管理重认证）。余额仍低于停止阈值时，下一次读数会再次关闭。
- 测试环境与正式环境的总闸互不影响。

## 5. 放行名单（分阶段开放）

- `allowlist_required(environment, risk)`：有 `risk` 段时取 `allowlist_only`；没有时正式环境为真、测试环境为假。
- 条目是付款方（`user` 按登录邮箱加入，`organization` 按组织编号加入），只追加；移出填写一次结束列。加入需要理由与管理重认证
  （它让钱可以流动）；移出只需理由。名单只决定能否发起付费调用，钱包、预算、额度与其他上限照常检查。

## 6. 上游余额监控

- `UpstreamBalance.poll` 用 `ProviderSettings.resolve()` 取得的密封平台密钥请求 `GET https://api.deepseek.com/user/balance`：
  地址固定、不跟随重定向、不读代理环境变量、响应上限 64 KB；密钥只在请求头里，请求后立即丢弃。异常一律化为代码
  （`upstream_timeout`、`upstream_unreachable`、`upstream_http_<状态码>`、`upstream_bad_response`、`currency_mismatch`），
  因为异常文本可能回显请求头；日志与读数里从不出现密钥（有测试断言）。
- 读数（`model_risk_balance_readings`）只存状态、代码、可用标志、单位与余额（按单位精度**向下取整**，不让余额看起来更多）。
- 评估：上游报告不可用 → 关闭总闸；低于停止阈值 → 关闭总闸；低于预警阈值 → 每个 UTC 日提醒管理员一次。
- 未配置时如实记录 `not_configured`（`cost_policy_missing` / `provider_disabled` / `credential_unavailable`），不发请求。

## 7. 对账

- **每日对账**（`Reconciliation.daily`）：UTC 日结束 10 分钟后，取最接近两个边界、且在 `max(900 秒, 2 × 读取间隔)` 以内的成功读数，
  上游支出 = 起点余额 − 终点余额 + 管理员登记的上游充值；账本成本 = 读数区间内准入的调用的精确结算成本之和（分数，不取整）；
  用量待确认的调用的预留成本单独列出，不计入。差额 = 上游支出 − 账本成本，超出容差为 `drift` 并提醒；缺少读数为 `incomplete`
  （不是“一致”）并提醒；结果只追加保存，管理员可对任一已结束的 UTC 日重算（新结果追加，不覆盖）。
- 已知的合理差异来源，容差要覆盖它们：DeepSeek 未说明按请求开始还是完成定档（成本价按派发时刻定档）；中途断开或被取消的流
  是否计费未说明；读数不在精确的午夜；边界前后准入、跨边界结算的调用。
- **账本审计**：每个 UTC 日运行一次 `Ledger.audit_balances(rebuild=False)`，只比较缓存余额/余数与不可变分录，只报告差异，
  从不自动修复（修复是另行决定的维护操作）。
- **上游充值登记**：管理员登记已经完成的上游账户充值（金额、时间、理由），只用于对账，不改变任何用户余额。

## 8. 定时任务

`RiskMonitor` 由计量 Agent 组合（portal 角色，受治理的 DeepSeek 路径）与计量恢复线程一同启动、在关闭时停止；每 60 秒一轮，
数据库租约（120 秒）保证多个进程同一时刻只有一个执行。一轮：到期则读余额 → 暴露提醒 → 前一 UTC 日的定时对账（每日一次）
→ 账本审计（每日一次）。每一步独立，失败只按异常类型记录，不影响其他步骤。

## 9. 告警

经既有站内通知与邮件（读者偏好），不引入外部服务。管理员告警发给当前有效授权中含 `platform.config.manage` 的平台管理员
（最多 50 人）；投递失败不撤销保护性状态变化。参数只含代码、金额与固定词语，不含对话内容、载荷或密钥。

| kind | 接收者 | 触发 | 去重 |
|---|---|---|---|
| `billing.risk.brake_triggered` | 管理员 | 刹车锁定暂停 | 每条暂停一次 |
| `billing.risk.paid_paused` | 发起该调用的用户 | 付款方被刹车暂停 | 每条暂停一次 |
| `billing.risk.breaker_opened` | 管理员 | 总闸关闭（任一来源） | 每个关闭事件一次 |
| `billing.risk.low_balance` | 管理员 | 余额低于预警阈值 | 每个 UTC 日一次 |
| `billing.risk.reconciliation` | 管理员 | 每日对账 `drift` / `incomplete` / `error` | 每条结果一次 |
| `billing.risk.ledger_audit` | 管理员 | 账本审计发现差异 | 每条结果一次 |
| `billing.risk.pending_exposure` | 管理员 | 用量待确认的预留成本 > `pending_alert` | 每个 UTC 日一次 |
| `billing.risk.payment_webhook_failed` | 管理员 | 支付渠道调用 `alert_payment_webhook_failure(store, environment=, provider=, code=)` | 每渠道每代码每 UTC 日一次 |

`alert_payment_webhook_failure` 是给支付渠道工作包的挂钩：只接受渠道名与稳定代码，从不接受载荷、签名或客户信息。

## 10. 管理端

Account `/admin?tab=model-risk`（导航“配置与治理 → 付费风险控制”）。读取需要 `platform.config.view`；页面只显示金额、状态、代码与
标识，不显示对话内容；付款方邮箱只对持有 `platform.users.view` 的管理员显示，组织名只对持有 `platform.orgs.view` 的显示。

| 操作 | 接口（`/api/admin/billing/risk`） | 需要 |
|---|---|---|
| 关闭全部付费调用 | `POST /breaker/open` | `platform.config.manage`、理由 |
| 重新开启 | `POST /breaker/close` | 同上 + 管理重认证 |
| 解除暂停 | `POST /suspensions/{id}/lift` | 同上 + 管理重认证 |
| 加入放行名单 | `POST /allowlist` | 同上 + 管理重认证 |
| 移出放行名单 | `POST /allowlist/{id}/remove` | `platform.config.manage`、理由 |
| 登记上游充值 | `POST /topups` | 同上 |
| 重算某日 / 立即审计账本 | `POST /reconciliations/run` | 同上 |

每个修改带客户端幂等键；授权在写状态、审计与操作结果的同一事务内复核；同一键不同内容返回 409 `idempotency_conflict`；
校验失败不回显提交值。阈值本身在「模型成本」页设置。

## 11. 备份与恢复

暂停与总闸关闭先写安全日志再提交 SQL。恢复后按 id 重放：恢复的库里没有的暂停重新建立（`source=journal`），备份里已有的
（包括管理员之后的解除）保持原样；没有记录的总闸关闭在当前未关闭时重新关闭。恢复只会更保守，不会悄悄重新放开付费调用。

## 12. 未实现与边界

- 网关在上游 402 时调用 `trip_platform_breaker` 的接线属于网关工作包；本包提供并测试了该函数与关闭后的拒绝。
- 支付回调失败的告警需要支付工作包在回调收件箱记为 `failed`/`rejected` 时调用 `alert_payment_webhook_failure`；本包只提供并测试了挂钩。
- 没有按路线的在途上限：平台 `max_inflight` 覆盖两条路线合计，生产取值按并发配额最小的已启用路线设置（operations.md §11.2 规则 1）。
- 余额只读取一个币种（成本单位的币种）；多币种余额与汇率不在范围内。
- 对账按准入时刻归属调用，不把跨边界的结算分摊到两个窗口；差异靠容差与人工复核。
- 暂停不会自动到期；总闸不会自动重新开启。
- 浏览器验收（三语言、390px、键盘、读屏）尚未在真实浏览器中完成；类型检查、构建与前端单元测试已通过。
