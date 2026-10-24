# 钱包充值、拒付与财务（org-payments，2026-10-05）

所有者决定（agent_deepseek_prompt §6.1）：钱包币种人民币；支付渠道 Stripe Checkout（支持银行卡、微信支付、支付宝，由管理员按商户已开通资格选择），
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

### 可用支付方式（2026-10-08）

支付版本的 `payment_methods` 是 `card`、`wechat_pay`、`alipay` 的非空、不重复子集；服务端按此顺序规范化。
新界面不预选，要求管理员明确选择；选择不代表 Stripe 已批准该渠道或该币种，仍须核对商户资格并验收。
发布沿用财务管理权限、管理员重认证、理由、修订号和幂等审计。个人与组织充值共用这份配置；客户端不能扩大渠道。
`GET /api/auth/wallet/topup-options` 返回配置中的实际列表，未配置时为 `[]`。这不是 Stripe 的实时资格探测。

历史版本没有该字段时继续解释为三种方式，读取及未修改提交保留原 JSON 形状。订单的 `settings_revision` 固定渠道版本；
一旦当前版本明确指定渠道，旧客户端省略该字段时保留当前渠道；要改变它必须显式提交新的非空列表，不能因编辑运营方等其它字段意外扩大渠道。
修改只作用于新收银台，不阻止旧收银台经有效签名与账本校验入账，也不把 Stripe 拒绝偷偷降级为其它渠道。
仅选银行卡时不发送微信专属选项。使用既有
[Checkout 静态支付方式参数](https://docs.stripe.com/api/checkout/sessions/create)（2026-10-08 核对，在固定的 API 版本
`2026-04-22.dahlia` 下有效，见下节），不引入动态渠道扩张。

无需数据库迁移。新字段发布后，旧程序不能读取该新版本；因此先完整发布支持版本，再启用渠道配置。
发布新字段后，恢复构建须保留该字段读取支持（包括历史收据读取），优先向前修复并在管理员界面关闭新充值、保留凭据。
不能直接回退到不识别该字段的旧程序，也不能靠删除版本或改数据库伪装兼容；完整备份恢复仍遵守原支付对账与恢复门槛。

### Stripe API 版本（2026-10-13）

所有 Stripe 请求（创建收银台、诊断/发布门禁的两个只读 GET）都发送 `Stripe-Version: 2026-04-22.dahlia`
（`synwevo_identity.payments.STRIPE_API_VERSION`）。这是 2026-10-13 账户的默认版本，所以固定它当天不改变任何行为；
它的作用是：有人在 Stripe 后台升级账户默认版本时，本平台的请求不会被悄悄换一种方式解读。Webhook 端点自身的版本
在 Stripe 端配置，不受此头影响。

dahlia 早于 2026-09-30 的 endive：收银台仍使用 `payment_method_types[i]`，列出未开通的方式时 Stripe 以 400
（`param=payment_method_types`）拒绝整个请求——这正是上节发布门禁与 `payment_method_not_enabled` 要防止和说明的情况。

**以后迁移到 endive（本次不做）**：endive 删除了收银台的 `payment_method_types`（带上即 400），改用
`allowed_payment_method_types[i]`，作为 Stripe 动态计算出的可用方式的过滤器。迁移必须在**同一次提交**里同时：
把 `STRIPE_API_VERSION` 改为 endive 版本；把 `StripeCheckout.create_checkout` 的参数名改为
`allowed_payment_method_types`（两者不能分开：dahlia 不认识新参数，endive 拒绝旧参数）；核对
`payment_method_options[wechat_pay][client]=web` 在微信被过滤掉时是否仍被接受；更新 `CHECKOUT_PARAMS`
与原因映射（`param` 根名）；更新 `test_topup_provider_refusals.py` 中的版本与参数断言（测试替身拒绝另一版本的参数）；
在隔离实例用测试密钥创建一次收银台（不付款）验证，再更新本节。还要核对 endive 下的 webhook 事件形状
（`_facts`）——Webhook 端点的版本由 Stripe 端单独配置。endive 的 `allowed_payment_method_types` 是对 Stripe 动态
计算出的可用方式的**过滤器**，展示偏好会参与计算：那时默认配置的 `available` 才是“收银台会不会提供这个方式”的
正确信号，发布门禁（`payment_readiness._activated`）应改为同时要求 `available=true`。

### 已保存渠道的只读诊断（2026-10-09）

财务管理员可在支付设置中手动调用 `GET /api/admin/billing/payments/readiness`，使用当前已密封保存的凭据，读取
Stripe账户的`charges_enabled`、三个支付方式的账户能力（`capabilities.card_payments`、`wechat_pay_payments`、
`alipay_payments`）及**直接商户默认支付配置**的三个`available`标志与展示偏好。关闭新充值但保留凭据时仍可检查。
只检查保存版本，不验证尚未提交的新密钥或草稿。结果带支付/凭据修订号、检查时间；请求结束时再次检查当前财务读取
权限和支付修订号，变更后拒绝返回旧结果，HTTP响应不可缓存。网络不占用数据库写事务。

诊断不创建收银台、不付费、不改开关、不发布设置。没有默认配置、默认配置不唯一/停用、分页超过500个配置时，返回
明确状态和未知值；不将未知当成不可用或可用。请求最多两路并发，单响应限制512KiB，失败只给出固定错误，不回传商户
个人资料、账号/配置标识、提供方原文或密钥。生产没有测试替身或乐观成功回退。

`available` 是默认配置中的能力与展示偏好组合（Stripe 的定义：展示偏好为 on **且**该方式的能力为 active），
**不是商户资格的独立证明，也不是本平台显式支付方式请求的最终结果**。固定的 dahlia 版本下收银台用显式的
`payment_method_types`，不看展示偏好：Stripe 默认显示关闭不必然表示渠道没有获批（`available=false` 可能只是
Dashboard 里关掉了展示）。因此结果另给每个方式的 `activated`（2026-10-13）：

- `true`：账户能力为 `active`，或默认配置报告 `available=true`（按定义蕴含能力 active）；
- `false`（确定未开通）：能力处于其它状态（`inactive`、`pending`）；或账户的能力列表里没有这一项且默认配置也报告
  `available=false`（两处一致，生产 2026-10-13 的微信支付/支付宝正是如此）；或账户没给能力列表、默认配置报告
  `available=false` 且展示偏好为 on；
- `null`：没有任何一方面的证据，或两方面互相矛盾（例如能力 inactive 而 `available=true`）。

`available=true`/`activated=true` 都不保证收银台一定接受该方式；实际币种、金额和渠道能否使用，仍以真实收银台处理
和签名入账验收为准。银行卡默认可用不等于已完成真实付款验证。

Stripe 对密钥本身的明确回答不是“暂时不可用”：HTTP 401 且带 Stripe 错误对象（`error.type`）→ 409
`payment_credentials_rejected`（密钥无效、已撤销或已过期，重试无用）；403 且带错误对象 → 409
`payment_credentials_forbidden`（密钥不能读取账户，例如权限受限的密钥）。网络错误、超时、其它 HTTP 状态、无错误
对象的 401/403、无法解析（含嵌套过深）或超过 512KiB 的响应、检查繁忙，仍是 503 `payment_readiness_unavailable` /
`payment_readiness_busy`。结果另带 `api_version`（Stripe 响应头 `Stripe-Version` 报告的实际适用版本，
格式不符时为 `null`）与 `api_version_requested`（本平台固定发送的版本，见下节）。

### 发布门禁（2026-10-13）

2026-10-13 生产事故：启用的版本选了商户尚未开通的微信支付与支付宝，Stripe 拒绝创建每一个收银台，订单却一直显示
“正在核实”。因此发布**启用**的版本（`spec.enabled=true`）前，`POST /payments/publish` 在任何数据库事务之外调用与诊断
相同的只读检查（替换密钥时用新提交的密钥，保留时用已保存密钥），之后在写事务里重新检查管理员权限与修订号
（期间有别的版本发布则 409 `payment_revision_conflict`）：

- Stripe 拒绝了这个密钥（检查得到 `payment_credentials_rejected`）：409 `payment_credentials_rejected`，什么都不保存；
  确认字段**不能**覆盖——用这个密钥的每一个收银台都会被拒绝。管理员核对或更换密钥后重新发布。
- 所选方式中有确定未开通的（`activated=false`）：409 `payment_methods_unavailable`，`detail.methods` 列出这些方式
  （按 card、wechat_pay、alipay 顺序），什么都不保存；管理员取消选择后重新发布。确认字段不能覆盖这一拒绝。
- 检查运行了但不足以证明（任一所选方式 `activated` 不是 `true`，例如默认配置缺失/停用/不唯一而账户没给能力列表、
  展示关闭且能力未知；或 `charges_enabled` 不是 `true`）：409 `payment_methods_unverified`，
  `detail={acknowledgement:"acknowledge_unverified_methods", methods:[未能确认的所选方式], charges_enabled}`。
  未知不是“不可用”的证据，也不是“已核实”的证据：只能明确确认后发布。
- 密钥不能读取账户（`payment_credentials_forbidden`，例如权限受限的密钥仍可能创建收银台）：409
  `payment_credentials_forbidden`，`detail.acknowledgement="acknowledge_unverified_methods"`。
- 检查本身无法完成（网络/超时、其它 HTTP 失败、无法解析的响应、检查繁忙、已保存密钥无法读取）：503
  `payment_readiness_unavailable`，`detail.acknowledgement="acknowledge_unverified_methods"`。
- 后三种情况，同一请求带上 `acknowledge_unverified_methods: true`（必须是 JSON 布尔值）重发即可发布；幂等键与请求
  哈希不变，已完成的发布重放时不再访问 Stripe。
- 关闭的版本（`enabled=false`）不做检查（关闭充值从不依赖密钥是否有效）。请求字段先校验，无效请求不会访问 Stripe。

只有 Stripe 报告账户可以收款（`charges_enabled=true`）且每个所选方式 `activated=true` 时才是 `verified`。
成功结果与审计 meta 带 `methods_check`：`verified`、`unverified_acknowledged`（并记录
`acknowledge_unverified_methods: true` 与被确认覆盖的拒绝 `methods_check_cause`：`payment_readiness_unavailable`、
`payment_credentials_forbidden` 或 `payment_methods_unverified`）或 `not_required`；被拒审计 meta 带 `code`，
`payment_methods_unavailable` / `payment_methods_unverified` 另带 `methods`。管理界面按拒绝种类分别说明：
未开通的方式逐个列出并可一键取消选择；被拒绝的密钥只提示更换，不提供确认框；其余三种显示各自原因与确认框
（确认框只对给出它的那个原因有效，原因变化时清空）。

参考（2026-10-09读取）：[Stripe支付配置](https://docs.stripe.com/api/payment_method_configurations/object)、
[官方SDK关于available和显式支付方式的定义](https://github.com/stripe/stripe-python/blob/master/stripe/_payment_method_configuration.py)。
采用其只读状态检查，保留本项目既有显式渠道与治理流程，不改为动态扩张支付方式。

## 2. 充值流程

1. 账户页请求 `GET /api/auth/wallet/topup-options` 得到服务端定价的套餐、支付方式与上下限；`POST /api/auth/wallet/topups`
   只接受 `package_id` 或上下限内的 `amount_minor`（分），浏览器不能指定入账额度。要求已验证邮箱；配置 Turnstile 时
   要求 `challenge_token`（动作 `topup`）；每个账户的每小时/每天订单数、同时未完成订单数、每天金额上限由支付设置给出。
   限额检查与它放行的订单行在同一个写事务里：同时到达的两个请求（连点、批量试卡）不能一起越过限额。
2. 服务端写入不可变的 `billing_orders`（`exchange_version=cny-fen-identity-v1`：入账单位 = 分 × 10^(scale−2)，精确）
   与 `payment_topups` 生命周期行，再向 Stripe 创建 Checkout Session：`mode=payment`、`currency=cny`、
   `payment_method_types=<订单固定的配置版本所选方式>`，仅在包含微信时发送 `payment_method_options[wechat_pay][client]=web`、
   成功/取消地址回到账户源站发起充值的页面（个人：`/account/wallet?topup=<订单>&result=success|cancel`；组织：
   `/organizations/<组织>?topup=<订单>&result=success|cancel`）、订单号写入
   `client_reference_id` 与 metadata、`Idempotency-Key=checkout-<订单>`、有效期 1 小时，请求头固定
   `Stripe-Version`（见下文“Stripe API 版本”）。创建结果分三种（2026-10-13）：
   - **收银台已创建**：订单转为 `pending`，返回付款地址。
   - **明确拒绝**（`ProviderRefused`）：Stripe 以 HTTP 400/401/402/403/404 返回其文档化的错误对象（`error.type`），
     说明什么都没有创建；或配置的方式列表无法发送（什么都没发出）。订单在同一种受保护更新里直接转为 `failed`、
     `failure_code=provider_refused`，释放同时未完成订单的名额；接口 201 返回 `status=failed`、
     `creation_status=resolved`、`failure_reason`（`payment_method_not_enabled`：所选方式未在服务商开通，
     即 `param=payment_method_types|payment_method_options` 或方式未激活类代码；`provider_configuration`：
     401/403、认证/权限错误、密钥失效等，以及方式列表无法发送；`provider_rejected`：其它明确拒绝）。没有扣款。
   - **结果未知**（`ProviderUnavailable`）：网络错误、超时、5xx、409、424、429、`idempotency_error`、没有错误对象的
     4xx（如代理页面，或超过 64KiB、嵌套过深无法解析的响应体）或不可用的 200（含嵌套过深的响应体）。保留原订单为 `creating`，接口返回 202 / `creation_status=verifying`；
     未收到结果不代表远端没有创建收银台，绝不重新发起。
   两种非成功结果都只保存经过清洗的 `failure_detail`（例如
   `http_400;type=invalid_request_error;param=payment_method_types`：HTTP 状态与 Stripe 错误的 type/code/param，
   仅允许 `[a-z0-9_.;=]`、最长 200 字符；param 只接受本适配器发送的参数名，方括号按显式规则改为点号，其余记为
   `other`；形如账号、密钥、会话等标识的值记为 `other`），并各写一行 `log.warning`（订单号与同一份清洗后字段）。
   从不保存、记录或返回 Stripe 的 message、请求体、密钥或账号标识。`failure_detail` 是 `payment_topups` 的附加列
   （`suite_schema/payments.py` 的 `COLUMNS`，旧行为 NULL）；旧程序不读取它，回滚无需恢复数据，但回滚后的旧程序会把
   新的 `provider_refused` 订单当作普通失败、把结果未知的订单重新显示为“正在核实”。之后到达的有效签名会话事件
   （`checkout.session.expired` / `async_payment_failed`）写入新的 `failure_code` 时一并清空 `failure_detail`：
   签名事件证明收银台确实存在，先前“结果未知”的创建诊断已过时；入账时 `failure_code` 与 `failure_detail` 也一并清空。
3. Stripe 回调 Webhook：校验 `Stripe-Signature`（`t=` 时间戳与一个或多个 `v1=`；HMAC-SHA256(`"{t}.{原始请求体}"`,
   签名密钥)；常量时间比较；时间差 300 秒以内）。只有通过校验的事件写入持久收件箱 `payment_webhook_inbox`（按事件 ID
   去重；只保存白名单事实与请求体 SHA-256，不存客户姓名、邮箱、地址或卡信息）。同一事件 ID 携带不同事实被拒绝。
4. 入账只发生在 `checkout.session.completed` 且 `payment_status=paid`，或 `checkout.session.async_payment_succeeded`：
   调用 `Ledger.credit_verified_payment`，其校验回调在账本事务内重新读取收件箱行并核对订单、金额、币种、支付单号与
   付款状态。重复投递、同一付款的另一事件都不会重复入账。金额/币种/会话不符的事件记为 `rejected`、不入账、返回 200
   以停止重试；处理失败返回 500 让 Stripe 重试，账户进程的清理任务每小时也会重放未处理的收件箱行。
   **失败告警**：收件箱事件以 `failed` 或 `rejected` 结束（含重放路径 `process_pending`，以及入账时重放先到争议的
   `_revive`——CR rr-money-2 前该路径不告警），以及进不了收件箱的投递
   （签名 `signature_missing|malformed|mismatch|stale`、`payload_too_large`、`payload_invalid`、
   `environment_mismatch`、`event_conflict`），都调用 `alert_payment_webhook_failure`，通知有
   `platform.config.manage` 的平台管理员（站内与邮件），每个环境、渠道、代码每个 UTC 日一次。告警只带渠道名
   （`stripe`）与稳定代码（如 `amount_mismatch`、`payment_order_mismatch`、`processing_error`），从不带请求体、
   签名、事件 ID 或客户信息；告警送不出去也不改变返回给 Stripe 的结果。
5. 返回页轮询 `GET /api/auth/wallet/topups/{order_id}`（`pending|paid|failed|expired`、`creation_status`、
   `credited_at`，创建时被明确拒绝的订单另带 `failure_reason`）；
   `GET /api/auth/wallet/receipts/{order_id}` 只对已入账订单返回收据：订单号、金额、币种、付款方式（来自 Stripe 的
   `charge.succeeded`，未收到则为空，不编造）、付款时间（Stripe 记录这笔付款成功的时间：签名事件自身的 `created`，
存于收件箱 `provider_created_at`；未知时不显示，绝不用处理时间代替——复审 payments-3）、入账时间（本平台入账的处理时间，
重试或补跑时可能晚于付款时间）、运营方名称（发布时的设置版本）、Stripe 支付单号。只有发起人
   或该组织当前的付费管理者可以读取订单与收据；发起组织充值的人失去付款管理权后也不能继续读取。

### 充值请求恢复（2026-10-08，待本轮发布）

新界面在第一次请求前，将随机 `idempotency_key`、原套餐或金额及环境、发起人、付款方保存到当前标签页的
`sessionStorage`。不保存支付密钥、卡号、挑战令牌或 Checkout 地址。网络失败、双击及刷新恢复沿用原键与原参数；
服务端每次重新检查当前身份、付款权限和钱包冻结。同键异参返回 409；首次 201、重放 200、尚无可靠结果 202。
原键按 `environment + initiator_id` 隔离，付款方和原选择进入请求哈希；改价后重放仍读取原订单。

`payment_topup_intents` 与订单生命周期在同一写事务占位，不可更新、删除或替换；账本仍使用已有独立耐久事务。
外部挑战与支付网络调用不占数据库写锁。创建后权限或配置变化会阻止释放付款链接，但无法撤回浏览器此前已取得的链接。
只在原订单有可信 `paid/failed/expired` 终态时，界面允许明确重新选择金额、生成新请求。未知结果不按超时推断失败，
不自动再调用提供方；由后续有效签名事件解决。但创建请求要求的有效期（`expires_at`，1 小时）再过
`CREATING_GRACE_S`（15 分钟，容纳时钟偏差与回调延迟）之后，那次请求可能创建的收银台在 Stripe 一侧已经过期、不能再付款：
读取与重放都报告 `status=expired`、`creation_status=resolved`（不带 `failure_reason`），它也早已不占同时未完成订单的
名额（该计数一直只计 `expires_at` 未到的订单）。这是读取时的判断，不写库；数据库里仍是 `creating` 与
`failure_code=provider_unknown`。之后若到达有效签名的付款事件，入账路径不检查订单状态，照常且只入账一次。
当前没有主动查询提供方的对账操作界面。

这是兼容的新增表：旧订单不回填虚构请求键，旧调用省略键仍是独立下单，因此旧客户端没有跨请求恢复保证。
部署窗口必须先更新服务端再使用新版界面；回滚不得删除映射或未知订单，且恢复构建必须保留本节幂等契约后才能重新开放充值。
同一标签页刷新可恢复；关闭标签页、清除浏览器存储或换设备不保证找回客户端意图，服务端原键映射仍保留。

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
- **商户或币种与已入账付款不符**：拒绝（`dispute_payment_mismatch`），不扣回、不冻结，并提醒管理员——直接投递、清扫
  重放和入账后重放先到的争议（`_revive`）都一样，每个环境、渠道、代码每个 UTC 日一次。
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

**冻结经得起恢复备份。** 冻结在任何 SQL 写入之前先追加到安全日志（`synwevo_core/journal.py` 的 `payer_frozen`：
冻结 id、环境、钱包账户 id、付款方类型与 id、拒付 id，不含金额、邮箱或支付载荷）；写不进日志，拒付整笔不记录，
事件留在收件箱重试。恢复时不论备份时间都重放，并按冻结 id 应用：恢复的库里已有这项冻结（仍冻结，或管理员在备份前
已解除）就原样保留，绝不重新冻结；库里没有的才重新建立，钱包重新不能付款；库里没有这个钱包账户则无可冻结。
若拒付本身晚于备份（库里没有拒付记录），重建的冻结不带拒付关联，管理员凭日志里的冻结 id 解除
（`POST /api/admin/billing/freezes/{id}/lift`）；财务视图的冻结账户数会计入它。

## 4. 平台权限与财务视图

新增平台权限 `platform.billing.view`（查看）与 `platform.billing.manage`（支付设置、赠送、解除冻结），只属于最高管理员
与新角色 **财务**（`finance` = 概览 + 上述两项）；其他委派角色都没有。财务角色不能管理账户、工作空间或模型配置，也看不到
任何业务内容。授予角色的表 `platform_role_grants` 的角色 CHECK 用表重建方式加宽（`platform_access.widen_roles`）。

| 接口（前缀 `/api/admin/billing`） | 权限 | 说明 |
|---|---|---|
| `GET /payments` | view | 支付设置状态、Webhook 地址与事件列表；不含密钥 |
| `POST /payments/publish` | manage，危险操作 | 版本化发布；理由必填、幂等、审计；可替换/撤销密钥（仍有未到期收银台时撤销被拒绝，见下） |
| `GET /finance` | view | 已入账充值、赠送（欢迎/管理员）、拒付与冻结、支付通知失败数、未使用余额；每笔充值带 `failure_code`、清洗后的 `failure_detail` 与告诉付款人的 `failure_reason`（结果未知且已过有效期加宽限的订单显示 `expired`） |
| `POST /grants` | manage，危险操作 | 赠送（`grant` 分录，理由必填、幂等、审计），个人按邮箱、组织按 ID，单次最多 10000 元 |
| `POST /freezes/{id}/lift` | manage，危险操作 | 解除拒付冻结，理由必填、审计 |

**未使用余额（基准日）**：每年 3 月 31 日与 9 月 30 日日终（日本时间 24:00，即日本预付式支付手段申报的基准日）所有个人
与组织钱包的余额合计（含赠送额度，并按分录类型拆分），以及当前值。计算只读不可变账本分录，不估算。

## 5. 组织付费

组织钱包（`org_wallet`）由持有组织权限 `org.billing.manage` 的人充值（所有者默认持有；管理员默认**不**持有，可通过
自定义角色授予）。谁是组织的付费管理者只由一个函数判定：`synwevo_core.billing.org_payers.manages_billing`（有效组织的
有效成员，基础角色或自定义角色带该权限）；充值接口、下列付费设置接口与用量/流水读取接口都调用它，所以能为组织付款的人
就是能读组织账单的人。付费设置在账户源站：

| 接口（前缀 `/api/auth/organizations/{oid}/billing`） | 说明 |
|---|---|
| `GET` | 组织钱包、每位成员的授权（是否允许、每月额度、本月已用与预留） |
| `PUT /members/{uid}` | `{enabled, monthly_limit_minor, expected_revision}`：允许某成员用组织付费；版本冲突返回 409 |
| `GET /statement` | 组织钱包流水；每笔消费标明是哪位成员（只有金额与元数据） |

成员在对话中选择付款方（`GET /api/suite/agent/payers`，个人默认在第一位）。额度按 UTC 自然月计算，已结算消费加进行中
预留不得超过成员额度；撤回授权、成员离开、组织冻结或删除都会在下一次预留与下一次发送前生效。所有变更写审计
（`org.billing.member_grant`）。

**暂不提供按工作空间的每月额度**（§6.1 要求的“按空间额度”仍未完成）。账本可以按调用所归属的工作空间限额
（`OrgPayers.check` 读 `ChargeContext.workspace_id`），但组织付费的 Agent 运行还没有归属工作空间：一次对话可以进入此人
所在的任何工作空间，成员自报的工作空间拦不住在另一个空间里干活的调用。这样的额度不会生效，所以接口不接受、页面不显示
（2026-10-05 评审 money-3/authz-3）。要提供它，须先让组织付费的运行在报价时固定一个工作空间，并把整次运行（每次工具调用、
复制到其他空间、运行中修改范围、持续付款授权、恢复重放）都限制在该空间内，再在每次预留时按它核对。

**页面**：组织详情页的「组织付费」区（`services/identity/web/src/pages/org/OrganizationBilling.tsx`）只对持有
`org.billing.manage` 的人显示：组织钱包可用/冻结金额与拒付冻结标记；为组织钱包充值（与个人充值同一套服务端定价、
Turnstile 与限频，`payer_kind=organization`，只跳转到 Stripe 托管收银台）；从收银台返回组织页后轮询订单状态
（先快后慢，2 分钟后停止并提供“再次查看”），只有服务端报告 `paid` 才显示“已到账”与收据（金额、付款方式、到账时间、
收款方、支付单号，可打印），取消返回直接说明“没有扣款”，失败/过期提示可重新发起；每位成员的授权开关与每月额度（显示本月已用与冻结）；组织账单（按页加载，
每笔消费注明成员邮箱）。未配置计费环境时只显示“组织付费尚未开放”，充值未开放时显示原因，不出现假入口。金额以整数
单位用 BigInt 精确格式化；额度输入与服务端上限一致（每月最多 1,000,000 元）。三语言文案在 `org-billing.messages.ts`，
由 `pages/org/flow.validation.mjs` 检查完整性与权限门槛。

## 6. 验证

- `tests/integration/test_billing_grants_chargebacks.py`、`test_billing_welcome.py`：账本赠送、拒付、冻结、组织钱包、迁移。
- `services/identity/tests/test_wallet_topups.py`：17 项端到端（Stripe 以 `httpx.MockTransport` 测试替身，Webhook 用夹具
  签名密钥按官方算法签名）：未配置诚实关闭、服务端定价、Checkout 参数、限频（含并发请求不能同时越过）、Turnstile、签名/时效/多签名、只在已付时入账、
  重放与乱序、错配拒绝、收据、拒付冻结、组织充值。
- `tests/integration/test_billing_org_payers.py`、`apps/suite/tests/test_agent_payers.py`、
  `services/identity/tests/test_org_billing_routes.py`：组织授权、成员月额度（含跨月；账本层的工作空间额度机制）、不提供工作空间额度设置、撤回授权阻止发送、
  组织付款不动个人钱包、付款方列表、委派自定义角色、版本冲突与审计。
- `services/identity/tests/test_admin_payments.py`：设置发布与密钥不回显、环境与密钥模式绑定、权限矩阵、赠送、财务视图与解冻、
  基准日、旧授权表加宽。`test_platform_access.py` 的路由权限矩阵包含新路由与财务角色。其 `world` 夹具装入
  `fixture_readiness`（MockTransport 测试替身），任何发布都不会访问网络。
- `services/identity/tests/test_topup_provider_refusals.py`（2026-10-13）：每个收银台请求带固定 `Stripe-Version`、仍发送
  dahlia 参数；模拟 dahlia 账户只开通银行卡的替身对微信/支付宝返回 400 `param=payment_method_types` → 订单
  `failed`/`resolved`、`failure_reason=payment_method_not_enabled`、释放 `max_pending` 名额、重放相同且不再调用、
  恰好一行不含 message/密钥的告警；401/403/402/404 与方式列表无法发送的原因映射；500/503/409/424/429、
  `idempotency_error`、无错误对象的 4xx 与网络失败保持 `creating`/`verifying`；卡住的创建在有效期加宽限后读作
  `expired`/`resolved`、不占名额，之后的签名付款事件仍只入账一次；清洗规则。
- `services/identity/tests/test_payment_publish_guard.py`（2026-10-13）：发布门禁（确定未开通的方式 409 且不能确认
  覆盖、展示关闭但能力 active 的方式为 verified、只有确定证据才拒绝、不足以证明时 409 `payment_methods_unverified`
  须确认且审计记录原因、被拒密钥 409 不能覆盖、受限密钥 409 可确认、无错误对象/5xx/429/嵌套过深的响应仍为可确认的
  503、保留密钥时用已保存密钥并在写事务内复查修订号、繁忙、关闭版本不检查、无效请求不访问 Stripe、重放不再检查）、
  诊断返回 `api_version`、财务视图显示原因。`test_payment_readiness.py` 另测 `activated` 的证据规则与 401/403 分类。
- 没有调用真实 Stripe：Checkout 参数名与签名算法按 docs.stripe.com（2026-10-05 读取）实现；真实测试模式联调（含微信支付/
  支付宝在 CNY 下的可用性与最低金额）须在隔离实例用 Stripe 测试密钥完成并记录证据。

## 7. 未实现 / 待所有者

- 退款：按所有者决定不提供。条款、定价页的“不退款”文字由公开页面工作包落地。
- 发票/税务、Stripe 对 CNY 结算与各支付方式最低金额的账户级核实。
- 争议结案后的自动处理；欠款的自动追收（目前冻结 + 人工处理）。
- 3-D Secure 由 Stripe Checkout 按其风控自动触发；本平台不另行配置。
