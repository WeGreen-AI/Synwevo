# 钱包、充值与用量（账户源）

Accounts 的 `/account/wallet` 使用本人当前会话读取额度、流水与用量。工作空间切换不改变付款人；平台管理员没有代读其他人钱包的入口。这些页面是已入账事实的投影：读取不创建任何资金事实，唯一的写入是本人自己的支出上限与低余额提醒。充值订单、支付回调与入账由 org-payments 包的充值接口负责（见下文“充值接口（消费方）”），本文件的页面只消费它。

## 配置与边界

启动时固定 `SYNWEVO_BILLING_ENVIRONMENT=test|live`，并显式提供 `SYNWEVO_BILLING_WALLET_UNITS`：由 1–16 个 `{code,scale}` 对象组成的 JSON 数组（钱包币种按所有者决定为 CNY）。单位须与既有账本定义相符，不允许浏览器选择精度或环境。没有配置环境或钱包单位，就是这个部署不提供钱包（生产默认如此，是有意的关闭，不是故障）：钱包接口返回 404 `wallet_not_offered`“这个部署没有提供付费 Agent 钱包”，认证状态 `/api/auth/state` 的 `wallet_offered` 为 false，账户页顶栏、账户导航与组织页都不显示钱包入口，直接打开钱包、用量或收据页只说明“没有提供”，没有“重试”。配置不一致、未知单位或存储暂不可用时返回 503 `wallet_unavailable`，页面显示不可用，不显示模拟余额。

没有默认币种、零售价或充值额。读取工厂创建的 Ledger 不含付款授权或支付验签回调，不能用于新增扣费或充值。所有生产写入 namespace 的到期恢复服务仍须部署，本页面不替代恢复 worker。

## 付款方与授权

所有读接口接受共享契约的付款方 `payer_kind=user|organization` 与 `payer_id`：

- `user`（缺省）只能是当前登录本人；指定其他用户 ID 一律 403 `wallet_forbidden`。平台管理员、工作空间角色都不是付款方。
- `organization` 只对该组织**当前的付费管理者**开放：组织 owner，或基础角色/自定义角色带 `org.billing.manage` 的成员（与充值接口同一规则，由 `synwevo_core.billing.usage.manages_billing` 每次按当前状态判定）。没有该权限的组织 admin、普通成员、其他组织成员、平台管理员，以及不存在或已删除的组织，得到同一个无法区分的 403。
- 组织的付费管理者能看到每次调用由哪位成员发起（他们本来就能看到成员名单），但**看不到成员的对话**：组织视角的调用行没有对话 ID 与标题，用量也不能按对话分组（只能按日、按模型、按成员）。
- `GET /api/auth/wallet/payers` 列出本人可读的付款方：本人在前，其后只有本人是付费管理者的有效组织（最多 50 个）。

## 接口（账户源，全部 `private, no-store`）

| 接口 | 返回 |
| --- | --- |
| `GET /api/auth/wallet` | 当前环境、付款方、各单位 `booked/used/held/available` 与 `used_through_seq` |
| `GET /api/auth/wallet/payers` | 本人可读的付款方列表 |
| `GET /api/auth/wallet/statement` | `after_seq` 从早到晚分页；或 `before_seq` 从新到旧分页（“最近动态”），`limit` 1–100 |
| `GET /api/auth/wallet/usage?group=day\|model\|conversation&from=&to=&tz=` | 时间段内按日 / 模型 / 对话（组织：成员）汇总的调用次数、已结算费用、预留中金额与 token 构成，及各单位合计 |
| `GET /api/auth/wallet/calls?from=&to=&tz=&model=&state=&cursor=&limit=` | 每次调用，最新在前；`next_cursor` 为不透明十进制游标 |
| `GET /api/auth/wallet/calls.csv?from=&to=&tz=&model=&state=` | 与表格完全相同的筛选结果，CSV 附件 |
| `GET/PUT /api/auth/wallet/limits` | 本人的日/周/月支出上限（既有 `PayerBudgets`，乐观 `expected_revision`） |
| `GET/PUT /api/auth/wallet/reminder` | 本人的低余额提醒线（`billing_wallet_reminders`） |

时间段是读者所在时区（账户时区，页面可显式传 `tz`）的日历日，含首尾，最长 366 天，缺省最近 30 天；未知查询参数、重复参数、非规范十进制、非法时区或日期一律 400，不回显输入。

每次调用的字段（共享契约“Usage read API”）：`model`、`band`（定价包钉在尝试上的 `peak/off_peak/flat`；尚未记录时为 `null`，页面写“时段未记录”，从不猜测）、`price_version`（售价版本）、`tokens.input_cached/input_uncached/output/reasoning`（只来自终止块的可信用量，思考 token 已包含在输出中）、`charged_minor`、`held_minor`、`state`（`settled/pending/held/not_charged`）、`finish_reason`、`dispatched_at/completed_at/duration_s`、`conversation_id`（及本人可见的对话标题；组织视角为 `member_user_id/member_name`）。

数字全部来自不可变的账本与计量事实（`billing_calls`、`billing_attempts`、`billing_usage`、`billing_settlements/billing_journal`、`agent_model_observations`），**从不读取引擎计数器**（如 `agent_turns.input_tokens`）。平台成本价与成本金额、提供方请求 ID、证据引用、调用的授权上下文都不输出。派发前就释放的调用从未动用付款人的钱，不列出；预留已过期的调用按账本规则显示为“未扣费”。

### CSV 安全

`calls.csv` 以 UTF-8 BOM 开头（表格软件可读中文标题），`Content-Disposition: attachment`、`X-Content-Type-Options: nosniff`，文件名由服务端按时间段生成。**每个单元格**都经过 `neutralize()`：去掉前导空格后以 `= + - @ | %`、制表符、回车、换行或全角 `＝ ＋ － ＠` 开头的，前面加 `'`，表格软件只把它当文本（OWASP CSV injection）。对话标题、成员名、模型名与结束原因来自外部，金额与 token 由本代码生成，同样经过处理。单次导出最多 10,000 行，汇总最多 20,000 次调用；超出返回 422，请缩短时间段。

### 支出上限与低余额提醒

支出上限是既有的 `PayerBudgets`：服务端在每次付费调用前检查。低余额提醒只是本人的偏好，不改变资金、不阻止调用：账户服务的后台清扫在可用余额（已入账余额减未过期预留）低于提醒线时，经通知目录发一条 `wallet.low_balance`（站内，邮件按本人通知设置），回到提醒线以上后再次跌破才会再提醒；认领与通知在同一事务提交。两个设置都需要 CSRF，审计只记录单位、修订号与开关，不记录金额。表 `billing_wallet_reminders` 与索引 `billing_calls_account_created` 由 `suite_schema/wallet_usage.py` 增量创建（`CREATE ... IF NOT EXISTS`，旧代码忽略），迁移测试见 `test_wallet_usage_schema_is_additive_and_idempotent`。

## 页面

- **钱包** `/account/wallet`：付款方选择（本人与可读的组织）、可用余额与构成、刷新、充值、最近动态（从新到旧，可加载更早记录；未知的分录类型按方向写“其他入账/其他扣减”，不显示原始代码）、本人的支出上限与低余额提醒。组织页向付费管理者提供“打开组织钱包”链接。测试环境显示明确的测试提示。
- **充值对话框**：只显示服务端 `topup-options` 返回的套餐与金额上下限；`available:false`（或本部署没有充值服务，404）时显示“充值暂未开放”等如实说明，**没有任何付款控件**。Turnstile 跟随账户服务对 `topup` 动作的挑战设置，被要求挑战的拒绝会启用组件；429 说明多少秒后再试。只跟随 `https` 且不含凭据的收银台地址。金额从不由前端决定。
- **返回页** `/account/wallet?topup=<order_id>&result=success|cancel`：轮询订单状态（1.5 秒起指数退避，最长 10 秒间隔，3 分钟后停止并提供手动刷新）；待确认 / 已到账 / 未完成 / 已过期 / 离开付款页 / 确认较慢 各有说明与下一步；离开付款页仍继续核对（付款可能在之后完成）。终态把焦点移到结果标题，状态以 `aria-live="polite"` 播报；到账时绘制一次成功标记（`prefers-reduced-motion` 下为静态）并刷新余额。
- **收据** `/account/wallet/receipts/:order_id`：只列服务端返回的字段（缺失的字段不显示），写明“充值后不可退款、余额只能用于 Synwevo 服务”，可打印/存为 PDF 或下载文本。
- **用量** `/account/wallet/usage`：时间段预设或自定义日期、分组切换（`aria-pressed`）、合计与 token 构成、图表（按日每天一列，无调用的日子是真实的 0；其他分组按服务端顺序最多 12 行，其余合并为一行“其他”；长度由整数比例计算）旁边是包含全部数值的表格；每次调用表格可按模型/状态筛选、游标翻页，CSV 导出与表格完全一致。窄屏下表格在可聚焦、具名的滚动区域内左右滚动，页面本身不横向溢出。
- 导航：账户顶栏为纯文字“钱包”，抽屉中为对齐的图标位；钱包子页面返回钱包，钱包返回账户设置。全部句子有简体、繁体与英文。

## 充值接口（消费方）

页面按共享契约调用 org-payments 包实现的接口，并依赖以下约定（若实现方不同，需要同步修改页面）：

- `GET /api/auth/wallet/topup-options` 的 `reason` 已有文案：`not_configured`、`email_unverified`、`wallet_frozen`、`not_permitted`；其他值按“暂未开放”显示。可选字段 `custom_amount:false` 隐藏自定义金额。
- `POST /api/auth/wallet/topups` 的 Turnstile 动作名为 `topup`，令牌字段由账户服务的挑战组件给出。
- 收银台的成功/取消返回地址应为 `/account/wallet?topup={order_id}&result=success|cancel`。
- `GET /api/auth/wallet/receipts/{order_id}` 以 JSON 返回 `order_id/status/amount_minor/currency`，可选 `method/paid_at/credited_at/payer_kind/provider_reference/operator_name`。

## 当前验证范围

金额显示测试覆盖简繁英、大于 2^53 的整数、负微额及 18 位单位精度。2026-09-30 隔离 SQLite 账本含 10,001 条合成流水，单单位/单并发顺序读取30次：中位2.289ms、P95 2.848ms、最大2.904ms；不含HTTP、模型、真实支付或多实例负载。输出在 `/private/tmp/synwevo-wallet-bench.json`。这不是生产SLA。

25项钱包测试及账户自助/隐私联合回归通过（统计见`/private/tmp/synwevo-wallet-http.xml`）；另100项账本/Agent/价格关联回归通过。Accounts类型检查、25项前端测试与构建通过。浏览器仍拦截本任务隔离页面地址，钱包的实际视觉、键盘与移动操作尚未验收；充值、退款、正式计费及真实模型链路不因本页面存在而达标。

- 账户套件 `services/identity/tests/test_wallet_usage.py`、`test_wallet_routes.py` 通过（组织钱包余额一项在账本尚无 `org_wallet` 账户类型时跳过，等 org-payments 的账本变更合入后自动生效）。变异验证：去掉“本人才能读本人”的判定、把组织 admin 当作付费管理者、`neutralize()` 不再忽略前导空格，分别使对应测试失败。
- 前端 `services/identity/web/tests/wallet-pages.test.mjs`（夹具数据渲染：付款方、时间段、金额精度、图表、充值关闭状态、返回页、收据、三语言目录）与账户前端其余测试、类型检查通过。
- 2026-10-05 用本地夹具页面（不提交）在 390px 宽度检查钱包、充值对话框、用量、收据与返回页：无页面横向溢出，对话框焦点在内，到账后焦点落在结果标题；简体与繁体渲染正确。真实充值、真实支付回调与真实模型链路不在本页面验证范围内。

## 币种、欢迎额度（org-payments，2026-10-05）

所有者决定（§6.1）：钱包币种为人民币，代码为 `CNY` 的钱包单位承接付款与赠送；精度至少到分（`scale >= 2`），
账本中的整数是 `10^-scale` 元，付款的 `amount_minor` 是分，换算是精确的 `分 × 10^(scale-2)`（`exchange_version`
固定为 `cny-fen-identity-v1`），没有汇率、没有取整。

**欢迎额度**：设置 `SYNWEVO_BILLING_WELCOME_MINOR`（分，1–10000；所有者定为 100 即 1 元）后，账户在首次使用
Agent（首次获取运行报价或打开付款人列表）时获得一次赠送分录（`grant`，执行者 `system:welcome`）。不设置即没有赠送，
页面也不提赠送。防滥用限制（均有测试）：

| 限制 | 实现 |
|---|---|
| 只给已验证邮箱的有效账户 | 未验证、停用、删除、应用身份账户返回 `email_unverified` / `ineligible`，不入账 |
| 每个账户一次 | `grant_id = welcome:<用户>`，幂等；并发首次使用只入账一次（唯一索引） |
| 每个邮箱一次 | 赠送行记录规范化邮箱（小写、去掉 `+标签`）的 SHA-256；删除账户后用同一邮箱重新注册不再赠送 |
| 平台每日上限 | `SYNWEVO_BILLING_WELCOME_DAILY_CAP`（默认 200，滚动 24 小时），超出返回 `capacity`，不静默给 0 |
| 人机验证 | 注册本身在配置 Turnstile 时已要求验证 |

所有者提到的“同设备/同支付指纹”限制未实现：平台不采集设备指纹，赠送发生在任何付款之前。
钱包接口的每个单位新增 `frozen`（发生拒付后被冻结，不能发起新的付费调用），流水新增 `grant`（赠送）与
`chargeback`（拒付追回）两种类型。
