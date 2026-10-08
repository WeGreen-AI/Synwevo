# DeepSeek 正式开放的剩余配置

代码发布与真实收款分开核对；当前部署状态以 STATE.md 为准。隔离真实模型、账本、跨空间、界面证据在 evidence/2026-10-06-deepseek/。

## 主机配置（由已授权运维完成）

现有位置 `/opt/synwevo/config/runtime.env`，不是源码或公开变量。正式环境使用 `SYNWEVO_AGENT_PROVIDER=deepseek`、`SYNWEVO_BILLING_ENVIRONMENT=live`；钱包单位 JSON 必须与批准的币种/价格精度一致。2026-10-05T17:57Z 已为正式后台预置 CNY/scale=6（微元精度），不构成售价发布或货币兑换。原配置备份 `/opt/synwevo/config/runtime.env.before-agent-config-20261005T175714Z`；2026-10-06T01:40Z 已由既有 CI/CD 随961acbf加载。欢迎额度按分配置，100 表示人民币1元；按用户明确的免费“1块”要求落实为每个已验证账户/邮箱一次人民币1元。2026-10-05T19:23:01Z 已将 `SYNWEVO_BILLING_WELCOME_MINOR=100` 预置到生产运行配置，备份 `runtime.env.before-welcome-20261005T192301Z`，沿用既有每日200次总量与账本防重复规则；2026-10-06发布后已核对Accounts与Portal实际加载，Agent仍关闭。本任务未调用生产赠送接口。生产不设置测试 upstream。

在下面的后台配置与定向正式验收完成前，保留当前应用名单，不加入 `agent`；开放时保留其它应用并经既有部署/运维方式重建 portal。代码发布本身不会启用模型或充值。

## 当前配置进度（2026-10-08）

已通过真实管理员登录/重认证发布分时日历v1（2026-10-07T14:58:27Z，ao_d7da658ed75f）；生产只读确认唯一版本1，勿重复发布。仅登记此次有效期相关的2026国庆假期，不宣称完整年度日历。价格/成本/提供方及付款方放行仍待发布；Mac已恢复，成本草稿mpd_3870d36aa953/rev1已保存；发布窗口要求重新验证身份，已请用户在站内完成口令或安全密钥重认证，不能通过直写数据库或伪造会话继续。Agent应用开关尚未打开。

首发参数已准备并经真实校验器/网关公式检查：evidence/2026-10-08-agent-activation/activation-preparation.json。单Flash、官方分时成本、拟2倍售价、输入65536/输出8192、none/low、平台日成本20元、并发4、个人并发1、放行名单；欢迎额度仍1元。2倍为准备值而非已发布价格，依据统一需求§28.5已请用户决定2倍/1倍，尚未收到答复；实际生效时间、期限与价格ID在发布时确定。2026-10-08T02:20Z官方只读检查已确认CNY余额高于准备的1元停止及21元预警阈值；较晚发布时仍须核对最新余额。配置文件不含密钥或会话。

12项约束及节假日转换检查结果见preparation-validation.json；只验证参数，不冒充生产调用。官方来源：[人民币价目](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)、[账户并发与隔离](https://api-docs.deepseek.com/quick_start/rate_limit/)，2026-10-08再次读取。先用欢迎额度验证Agent，真实充值准备另行完成；公开放量继续遵守operations.md §11.3的连续数日对账门槛。

## 现有后台门禁

1. 正式平台管理员登录 Account；使用真实密码或 Passkey 完成 `scope=admin` 重认证。没有可替代此门禁的 provider 运维 CLI；不直接写库或伪造会话。依据：`services/identity/synwevo_identity/admin_model_provider.py`、`packages/core/synwevo_core/platform_access.py`。
2. `/admin?tab=model-prices`：录入已核对的成本价；管理员设定加价倍率并发布售价。跨币种不自动假设汇率。价格需有真实来源与有效版本。
3. `/admin?tab=model-costs` 与 `model-risk`：发布成本总上限、单次/周期/并发限额及放行名单。`docs/operations.md §11` 是参数约束说明，不是已执行的配置。
4. `/admin?tab=model-provider`：填写官方已返回的模型 `deepseek-flash`、证据引用、价格版本、上下文/输出上限与有效期；通过受治理密钥字段密封保存。默认未完成调用均不收费，管理员可按类型修改并发布。不得将真实密钥放入 runtime.env、Git、聊天或日志。
5. 放行隔离验收付款方，完成真实模型、工具、账单、停止与恢复验证后再扩大开放。钱包或个人限额不足时必须拒绝发送。

## 真实充值尚缺

2026-10-07用户已确认使用现有**香港个人商户**（Stripe展示名Hiko Sphere）收款；不再等待另一家公司的资料。用户指定的本机 `strip_api.txt` 已仅通过Stripe官方 `GET /v1/account` 做只读核验：live、HK、individual、默认HKD、charges/payouts enabled、card_payments active。该文件已被Git忽略且未跟踪，权限0600；密钥没有复制到证据、前端、runtime.env或日志。默认HKD不改变平台既定CNY钱包/收款币种，也不代表所有渠道/币种已可用。

仍需平台真实管理员在既有重认证门禁内发布支付设置、配置Webhook签名密钥，核实CNY及微信/支付宝资格并完成受控真实充值/回调/入账验证。真实回调地址为 `https://accounts.synwevo.com/api/auth/payments/stripe/webhook`；事件列表和验收见 `docs/platform/wallet-payments.md`。已知Stripe展示名不当作未经返回/核实的法定姓名。脱敏账户证据为 `evidence/2026-10-07-webhook-reliability/stripe-account-readiness.json`；接口依据：[Stripe Retrieve account](https://docs.stripe.com/api/accounts/retrieve)（2026-10-07读取）。本轮未创建收银台、扣款、创建Webhook或发布支付配置。

2026-10-07T12:46Z完整只读列表补验：该live账户默认支付配置中card可用且on；alipay/wechat_pay均不可用且off；指定Synwevo回调匹配数量0。见stripe-channel-readiness.json。当前产品Checkout固定请求三种支付方式，正式充值开放前必须先解决商户渠道资格或实现/批准可用渠道选择，不能假定仅有卡资格就能接受现有三渠道请求；本轮未修改支付行为。官方接口：[Webhook列表](https://docs.stripe.com/api/webhook_endpoints/list)、[支付方式配置列表](https://docs.stripe.com/api/payment_method_configurations/list)。

`/admin?tab=payments` 未正确配置之前，充值返回 unavailable，不创建假订单；不能用测试钱包赠送证明真实充值成功。组织付款沿现有 `org.billing.manage` 与成员支出授权。

DeepSeek 针对 API 数据训练与保留期限的书面答复仍未取得；未代发外部邮件、未承诺零训练/零保留。

恢复：在本工作树读取 MASTER_PROMPT.md、STATE.md、BACKLOG.md、EVIDENCE.md，核对单协调者锁与真实 Git/CI/服务器版本，从最新具体断点继续，勿重复已完成模型运行、赠送或充值。

## 向提供方索取 API 专门书面说明（草稿，尚未发送）

2026-10-06 核对：官方 [API 联系入口](https://api-docs.deepseek.com/api/deepseek-api/) 列出 `api-service@deepseek.com`。下列草稿不含账号、密钥或用户数据；发送身份/公司须由实际经营者补充。收到正式答复前继续保留公开页面的不确定性说明。

主题：Synwevo 应用使用 DeepSeek API 的数据处理与商业条款确认

DeepSeek 团队您好：

我们正将 DeepSeek 官方 API 接入 Synwevo 的付费个人 Agent。用户通过 Synwevo 使用应用功能，由平台承担 API 费用；主密钥保留在服务端，不向用户分发。请针对开放平台 API（而非消费聊天产品）书面确认：

1. API 请求、回答、工具参数/结果和缓存是否用于预训练、微调、评估或人工标注；默认规则、退出方式及生效范围。
2. 上述数据、诊断/安全日志与备份分别保留多久；删除请求、删除时限及例外。
3. 处理和存储地区、分包方、传输保护，以及可提供的数据处理协议。
4. Synwevo 向其用户收费的应用集成模式适用哪些开放平台条款；如需额外手续，请提供具体条款和办理方式。
5. Synwevo 在模型选择、隐私说明和调用账单中如实显示 DeepSeek 名称的使用要求。
6. 流式中断、取消、超时且缺失最终 usage 时，上游计费及可核实的用量/请求对账方式。

请注明答复适用的账号类型、模型、条款版本与生效日期。谢谢。

2026-10-08T03:15:14Z更新：官方成本价price_85f4371761b6已通过真实管理员发布，复用此版本；正式零售倍率仍待所有者按MASTER_PROMPT§28.5确认。支付渠道修复96f73ab已推送，等待既有CI/CD，尚不能称真实充值或Agent启用完成。
