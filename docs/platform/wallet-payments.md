# 钱包充值、拒付与财务（org-payments，2026-10-05）

所有者决定（agent_deepseek_prompt §6.1）：钱包币种人民币；支付渠道 Stripe Checkout（银行卡、微信支付、支付宝），
以人民币收款；**充值后不退款**（条款与定价页须写明，本期没有退款流程）；拒付/争议仍要处理（冻结钱包、记录分录、
钱包不为负）；组织可以替成员付费。本页记录已实现的行为与边界，未实现的事项写在最后。

## 1. 开关与配置（默认全部关闭）

| 条件 | 未满足时 |
|---|---|
| `SYNWEVO_BILLING_ENVIRONMENT=test\|live` | 支付设置与财务页显示“尚未配置计费环境”，充值接口 `available:false, reason:not_configured` |
| `SYNWEVO_BILLING_WALLET_UNITS` 含 `{"code":"CNY","scale":≥2}` | 付款无法入账，充值关闭（`not_configured`） |
| Admin「支付设置」发布 `enabled=true` 的版本，且已存入 Stripe 密钥与 Webhook 签名密钥 | 充值入口显示“暂未开放”，没有假入口 |

密钥只经 Admin「支付设置」录入，以 `platform-payment-key` 用途密封存储（`sealed.py`，随根密钥轮换 `rekey`），
任何响应、审计、操作记录、日志都不包含明文；页面只显示“已配置”。测试环境只接受 `sk_test_`/`rk_test_` 密钥，
正式环境只接受 `sk_live_`/`rk_live_`，Webhook 签名密钥须以 `whsec_` 开头——测试实例不能扣真实卡，正式实例不能
静默跑在测试模式。不要把密钥放进 `runtime.env` 或 compose。

Stripe 后台需要把 Webhook 指向 `https://<账户域名>/api/auth/payments/stripe/webhook`，订阅：
`checkout.session.completed`、`checkout.session.async_payment_succeeded`、`checkout.session.async_payment_failed`、
`checkout.session.expired`、`charge.succeeded`、`charge.dispute.created`（设置页显示同一列表）。

## 2. 充值流程

1. 账户页请求 `GET /api/auth/wallet/topup-options` 得到服务端定价的套餐与上下限；`POST /api/auth/wallet/topups`
   只接受 `package_id` 或上下限内的 `amount_minor`（分），浏览器不能指定入账额度。要求已验证邮箱；配置 Turnstile 时
   要求 `challenge_token`（动作 `topup`）；每个账户的每小时/每天订单数、同时未完成订单数、每天金额上限由支付设置给出。
   限额检查与它放行的订单行在同一个写事务里：同时到达的两个请求（连点、批量试卡）不能一起越过限额。
2. 服务端写入不可变的 `billing_orders`（`exchange_version=cny-fen-identity-v1`：入账单位 = 分 × 10^(scale−2)，精确）
   与 `payment_topups` 生命周期行，再向 Stripe 创建 Checkout Session：`mode=payment`、`currency=cny`、
   `payment_method_types=[card,wechat_pay,alipay]`、`payment_method_options[wechat_pay][client]=web`、
   成功/取消地址回到账户源站发起充值的页面（个人：`/account/wallet?topup=<订单>&result=success|cancel`；组织：
   `/organizations/<组织>?topup=<订单>&result=success|cancel`）、订单号写入
   `client_reference_id` 与 metadata、`Idempotency-Key=checkout-<订单>`、有效期 1 小时。创建失败返回 502
   `topup_provider_unavailable`（“未扣款”），订单记为失败。
3. Stripe 回调 Webhook：校验 `Stripe-Signature`（`t=` 时间戳与一个或多个 `v1=`；HMAC-SHA256(`"{t}.{原始请求体}"`,
   签名密钥)；常量时间比较；时间差 300 秒以内）。只有通过校验的事件写入持久收件箱 `payment_webhook_inbox`（按事件 ID
   去重；只保存白名单事实与请求体 SHA-256，不存客户姓名、邮箱、地址或卡信息）。同一事件 ID 携带不同事实被拒绝。
4. 入账只发生在 `checkout.session.completed` 且 `payment_status=paid`，或 `checkout.session.async_payment_succeeded`：
   调用 `Ledger.credit_verified_payment`，其校验回调在账本事务内重新读取收件箱行并核对订单、金额、币种、支付单号与
   付款状态。重复投递、同一付款的另一事件都不会重复入账。金额/币种/会话不符的事件记为 `rejected`、不入账、返回 200
   以停止重试；处理失败返回 500 让 Stripe 重试，账户进程的清理任务每小时也会重放未处理的收件箱行。
5. 返回页轮询 `GET /api/auth/wallet/topups/{order_id}`（`pending|paid|failed|expired` 与 `credited_at`）；
   `GET /api/auth/wallet/receipts/{order_id}` 只对已入账订单返回收据：订单号、金额、币种、付款方式（来自 Stripe 的
   `charge.succeeded`，未收到则为空，不编造）、付款时间（Stripe 记录这笔付款成功的时间：签名事件自身的 `created`，
存于收件箱 `provider_created_at`；未知时不显示，绝不用处理时间代替——复审 payments-3）、入账时间（本平台入账的处理时间，
重试或补跑时可能晚于付款时间）、运营方名称（发布时的设置版本）、Stripe 支付单号。只有发起人
   或该组织当前的付费管理者可以读取订单与收据。

## 3. 拒付（争议）

`charge.dispute.created` → `Ledger.record_chargeback`：按争议金额占订单金额的比例（向上取整）计算应追回单位，
只从钱包可用余额（入账余额 − 进行中的预留）中扣回，绝不让钱包为负，差额记为欠款（`outstanding_units`）；同一事务内
冻结该钱包。冻结的钱包不能预留/派发付费调用，也不能再充值（`wallet_frozen`）。解除冻结是财务管理员带理由的审计操作；
拒付记录与欠款保留不变。争议结案（`charge.dispute.closed`）本期不自动处理：胜诉后的补偿由管理员带理由赠送。

边界（复审 money-2、money-4、payments-2，2026-10-05）：

- **争议金额高于付款金额**（Stripe 说明争议金额可能因汇率波动等与付款不同）：按付款金额封顶，照常扣回并冻结，
  不再当作失败无限重试。
- **同一笔付款的全部争议合计**最多扣回该付款入账的单位：同一扣款的第二个争议对象只扣回此前未扣的部分，
  不会动用另一笔未争议充值的余额；每个争议仍单独记录并冻结钱包。
- **询问**（Stripe 状态 `warning_*`，银行只是询问、未扣走资金）：只冻结、不扣回（记录 `inquiry=1`、争议单位 0）。
  冻结已经保证这笔余额不能再被花掉；若询问升级为新的争议对象，按上一条的封顶规则扣回。同一争议对象状态升级
  （`charge.dispute.updated` / `funds_withdrawn`）本期不处理，钱包保持冻结，由管理员处理。
- **商户或币种与已入账付款不符**：拒绝（`dispute_payment_mismatch`），不扣回、不冻结，并提醒管理员。
- **本平台没有入账的付款的争议**（同一 Stripe 账户的其他业务、被拒绝的充值）：本平台的结账从未报告过该付款时，
  拒绝（`dispute_payment_unknown`）并提醒管理员，不再无限重试；若该付款之后在这里入账（事件乱序），入账时立即应用
  这项争议。本平台的结账已报告该付款、只是尚未入账时，记为失败让 Stripe 重试，入账时同样立即应用。
- **补跑清扫**（`process_pending`）：尝试次数少的先处理，持续失败的行不会挤占崩溃遗留的新行；失败行至少间隔
  min(2^尝试次数 分钟, 1 天) 再试，总共最多 12 次（约等于 Stripe 自己三天的重试窗口），之后保持“失败”供管理员处理
  （财务视图计数）并提醒一次。

### 撤销密钥与未到期的收银台（复审 payments-1，2026-10-05）

撤销会删除 Webhook 签名密钥，此后任何支付通知都无法验证。若这时还有未到期的收银台（`creating`/`pending` 且
`expires_at` 未到，最长 `CHECKOUT_TTL_S` = 1 小时），顾客在那里完成付款会被扣款却永远不能入账。因此：

- 仍有这样的收银台时，`revoke` 被拒绝（409 `payment_revoke_open_checkouts`，附 `open_checkouts` 数量与最晚到期时间
  `open_until`）。`GET /payments` 的 `open_checkouts` 让设置页在选择撤销时就显示这一提醒。
- 推荐做法：先发布“关闭充值、保留密钥”（随时可以），等收银台到期后再撤销；关闭期间付款照常入账。
- 确需立即撤销（例如签名密钥本身已泄露，继续验证反而会接受伪造通知）时，管理员必须明确勾选
  `override_open_checkouts`；审计记录 `override_open_checkouts` 与当时的未到期数量，这些付款需要人工核对。

为什么不在撤销后继续保留旧签名密钥验证到期前的通知：撤销最常见的原因正是怀疑密钥泄露；保留泄露的签名密钥会让
伪造的“已付款”通知在窗口内入账。拒绝 + 明确覆盖让默认路径不伤害顾客，紧急路径由管理员知情决定。

## 4. 平台权限与财务视图

新增平台权限 `platform.billing.view`（查看）与 `platform.billing.manage`（支付设置、赠送、解除冻结），只属于最高管理员
与新角色 **财务**（`finance` = 概览 + 上述两项）；其他委派角色都没有。财务角色不能管理账户、工作空间或模型配置，也看不到
任何业务内容。授予角色的表 `platform_role_grants` 的角色 CHECK 用表重建方式加宽（`platform_access.widen_roles`）。

| 接口（前缀 `/api/admin/billing`） | 权限 | 说明 |
|---|---|---|
| `GET /payments` | view | 支付设置状态、Webhook 地址与事件列表；不含密钥 |
| `POST /payments/publish` | manage，危险操作 | 版本化发布；理由必填、幂等、审计；可替换/撤销密钥（仍有未到期收银台时撤销被拒绝，见下） |
| `GET /finance` | view | 已入账充值、赠送（欢迎/管理员）、拒付与冻结、支付通知失败数、未使用余额 |
| `POST /grants` | manage，危险操作 | 赠送（`grant` 分录，理由必填、幂等、审计），个人按邮箱、组织按 ID，单次最多 10000 元 |
| `POST /freezes/{id}/lift` | manage，危险操作 | 解除拒付冻结，理由必填、审计 |

**未使用余额（基准日）**：每年 3 月 31 日与 9 月 30 日日终（日本时间 24:00，即日本预付式支付手段申报的基准日）所有个人
与组织钱包的余额合计（含赠送额度，并按分录类型拆分），以及当前值。计算只读不可变账本分录，不估算。

## 5. 组织付费

组织钱包（`org_wallet`）由持有组织权限 `org.billing.manage` 的人充值（所有者默认持有；管理员默认**不**持有，可通过
自定义角色授予）。付费设置在账户源站：

| 接口（前缀 `/api/auth/organizations/{oid}/billing`） | 说明 |
|---|---|
| `GET` | 组织钱包、每位成员的授权（是否允许、每月额度、本月已用与预留）、每个工作空间的可选每月额度 |
| `PUT /members/{uid}` | `{enabled, monthly_limit_minor, expected_revision}`：允许某成员用组织付费；版本冲突返回 409 |
| `PUT /workspaces/{wid}` | `{monthly_limit_minor 或 null, expected_revision}`：工作空间每月额度（null 为不限） |
| `GET /statement` | 组织钱包流水；每笔消费标明是哪位成员、哪个工作空间（只有金额与元数据） |

成员在对话中选择付款方（`GET /api/suite/agent/payers`，个人默认在第一位）。额度按 UTC 自然月计算，已结算消费加进行中
预留不得超过成员额度与工作空间额度；撤回授权、成员离开、组织冻结或删除都会在下一次预留与下一次发送前生效。所有变更
写审计（`org.billing.member_grant`、`org.billing.workspace_limit`）。

**页面**：组织详情页的「组织付费」区（`services/identity/web/src/pages/org/OrganizationBilling.tsx`）只对持有
`org.billing.manage` 的人显示：组织钱包可用/冻结金额与拒付冻结标记；为组织钱包充值（与个人充值同一套服务端定价、
Turnstile 与限频，`payer_kind=organization`，只跳转到 Stripe 托管收银台）；从收银台返回组织页后轮询订单状态
（先快后慢，2 分钟后停止并提供“再次查看”），只有服务端报告 `paid` 才显示“已到账”与收据（金额、付款方式、到账时间、
收款方、支付单号，可打印），取消返回直接说明“没有扣款”，失败/过期提示可重新发起；每位成员的授权开关与每月额度（显示本月已用与冻结）；工作空间每月额度（留空为不限）；组织账单（按页加载，
每笔消费注明成员邮箱）。未配置计费环境时只显示“组织付费尚未开放”，充值未开放时显示原因，不出现假入口。金额以整数
单位用 BigInt 精确格式化；额度输入与服务端上限一致（每月最多 1,000,000 元）。三语言文案在 `org-billing.messages.ts`，
由 `pages/org/flow.validation.mjs` 检查完整性与权限门槛。

## 6. 验证

- `tests/integration/test_billing_grants_chargebacks.py`、`test_billing_welcome.py`：账本赠送、拒付、冻结、组织钱包、迁移。
- `services/identity/tests/test_wallet_topups.py`：17 项端到端（Stripe 以 `httpx.MockTransport` 测试替身，Webhook 用夹具
  签名密钥按官方算法签名）：未配置诚实关闭、服务端定价、Checkout 参数、限频（含并发请求不能同时越过）、Turnstile、签名/时效/多签名、只在已付时入账、
  重放与乱序、错配拒绝、收据、拒付冻结、组织充值。
- `tests/integration/test_billing_org_payers.py`、`apps/suite/tests/test_agent_payers.py`、
  `services/identity/tests/test_org_billing_routes.py`：组织授权、成员与工作空间月额度（含跨月）、撤回授权阻止发送、
  组织付款不动个人钱包、付款方列表、委派自定义角色、版本冲突与审计。
- `services/identity/tests/test_admin_payments.py`：设置发布与密钥不回显、环境与密钥模式绑定、权限矩阵、赠送、财务视图与解冻、
  基准日、旧授权表加宽。`test_platform_access.py` 的路由权限矩阵包含新路由与财务角色。
- 没有调用真实 Stripe：Checkout 参数名与签名算法按 docs.stripe.com（2026-10-05 读取）实现；真实测试模式联调（含微信支付/
  支付宝在 CNY 下的可用性与最低金额）须在隔离实例用 Stripe 测试密钥完成并记录证据。

## 7. 未实现 / 待所有者

- 退款：按所有者决定不提供。条款、定价页的“不退款”文字由公开页面工作包落地。
- 发票/税务、Stripe 对 CNY 结算与各支付方式最低金额的账户级核实。
- 争议结案后的自动处理；欠款的自动追收（目前冻结 + 人工处理）。
- 3-D Secure 由 Stripe Checkout 按其风控自动触发；本平台不另行配置。
