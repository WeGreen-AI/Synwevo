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
2. 服务端写入不可变的 `billing_orders`（`exchange_version=cny-fen-identity-v1`：入账单位 = 分 × 10^(scale−2)，精确）
   与 `payment_topups` 生命周期行，再向 Stripe 创建 Checkout Session：`mode=payment`、`currency=cny`、
   `payment_method_types=[card,wechat_pay,alipay]`、`payment_method_options[wechat_pay][client]=web`、
   成功/取消地址回到账户源站 `/account/wallet?topup=<订单>&result=success|cancel`、订单号写入
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
   `charge.succeeded`，未收到则为空，不编造）、入账时间、运营方名称（发布时的设置版本）、Stripe 支付单号。只有发起人
   或该组织当前的付费管理者可以读取订单与收据。

## 3. 拒付（争议）

`charge.dispute.created` → `Ledger.record_chargeback`：按争议金额占订单金额的比例（向上取整）计算应追回单位，
只从钱包可用余额（入账余额 − 进行中的预留）中扣回，绝不让钱包为负，差额记为欠款（`outstanding_units`）；同一事务内
冻结该钱包。冻结的钱包不能预留/派发付费调用，也不能再充值（`wallet_frozen`）。争议先于入账到达时，事件记为失败并由
Stripe 重试，入账后再处理，不会丢失。解除冻结是财务管理员带理由的审计操作；拒付记录与欠款保留不变。争议结案
（`charge.dispute.closed`）本期不自动处理：胜诉后的补偿由管理员带理由赠送。

## 4. 平台权限与财务视图

新增平台权限 `platform.billing.view`（查看）与 `platform.billing.manage`（支付设置、赠送、解除冻结），只属于最高管理员
与新角色 **财务**（`finance` = 概览 + 上述两项）；其他委派角色都没有。财务角色不能管理账户、工作空间或模型配置，也看不到
任何业务内容。授予角色的表 `platform_role_grants` 的角色 CHECK 用表重建方式加宽（`platform_access.widen_roles`）。

| 接口（前缀 `/api/admin/billing`） | 权限 | 说明 |
|---|---|---|
| `GET /payments` | view | 支付设置状态、Webhook 地址与事件列表；不含密钥 |
| `POST /payments/publish` | manage，危险操作 | 版本化发布；理由必填、幂等、审计；可替换/撤销密钥 |
| `GET /finance` | view | 已入账充值、赠送（欢迎/管理员）、拒付与冻结、支付通知失败数、未使用余额 |
| `POST /grants` | manage，危险操作 | 赠送（`grant` 分录，理由必填、幂等、审计），个人按邮箱、组织按 ID，单次最多 10000 元 |
| `POST /freezes/{id}/lift` | manage，危险操作 | 解除拒付冻结，理由必填、审计 |

**未使用余额（基准日）**：每年 3 月 31 日与 9 月 30 日日终（日本时间 24:00，即日本预付式支付手段申报的基准日）所有个人
与组织钱包的余额合计（含赠送额度，并按分录类型拆分），以及当前值。计算只读不可变账本分录，不估算。

## 5. 验证

- `tests/integration/test_billing_grants_chargebacks.py`、`test_billing_welcome.py`：账本赠送、拒付、冻结、组织钱包、迁移。
- `services/identity/tests/test_wallet_topups.py`：16 项端到端（Stripe 以 `httpx.MockTransport` 测试替身，Webhook 用夹具
  签名密钥按官方算法签名）：未配置诚实关闭、服务端定价、Checkout 参数、限频、Turnstile、签名/时效/多签名、只在已付时入账、
  重放与乱序、错配拒绝、收据、拒付冻结、组织充值。
- `services/identity/tests/test_admin_payments.py`：设置发布与密钥不回显、环境与密钥模式绑定、权限矩阵、赠送、财务视图与解冻、
  基准日、旧授权表加宽。`test_platform_access.py` 的路由权限矩阵包含新路由与财务角色。
- 没有调用真实 Stripe：Checkout 参数名与签名算法按 docs.stripe.com（2026-10-05 读取）实现；真实测试模式联调（含微信支付/
  支付宝在 CNY 下的可用性与最低金额）须在隔离实例用 Stripe 测试密钥完成并记录证据。

## 6. 未实现 / 待所有者

- 退款：按所有者决定不提供。条款、定价页的“不退款”文字由公开页面工作包落地。
- 发票/税务、Stripe 对 CNY 结算与各支付方式最低金额的账户级核实。
- 争议结案后的自动处理；欠款的自动追收（目前冻结 + 人工处理）。
- 3-D Secure 由 Stripe Checkout 按其风控自动触发；本平台不另行配置。
