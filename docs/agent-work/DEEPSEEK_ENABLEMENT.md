# DeepSeek 正式开放的剩余配置

代码发布与真实收款分开核对；当前部署状态以 STATE.md 为准。隔离真实模型、账本、跨空间、界面证据在 evidence/2026-10-06-deepseek/。

## 主机配置（由已授权运维完成）

现有位置 `/opt/synwevo/config/runtime.env`，不是源码或公开变量。正式环境使用 `SYNWEVO_AGENT_PROVIDER=deepseek`、`SYNWEVO_BILLING_ENVIRONMENT=live`；钱包单位 JSON 必须与批准的币种/价格精度一致。2026-10-05T17:57Z 已为正式后台预置 CNY/scale=6（微元精度），不构成售价发布或货币兑换。原配置备份 `/opt/synwevo/config/runtime.env.before-agent-config-20261005T175714Z`；未重启，交由既有 CI/CD 加载。欢迎额度按分配置，100 表示人民币1元；未确认前不把隔离值复制到生产。生产不设置测试 upstream。

在下面的后台配置与定向正式验收完成前，保留当前应用名单，不加入 `agent`；开放时保留其它应用并经既有部署/运维方式重建 portal。代码发布本身不会启用模型或充值。

## 现有后台门禁

1. 正式平台管理员登录 Account；使用真实密码或 Passkey 完成 `scope=admin` 重认证。没有可替代此门禁的 provider 运维 CLI；不直接写库或伪造会话。依据：`services/identity/synwevo_identity/admin_model_provider.py`、`packages/core/synwevo_core/platform_access.py`。
2. `/admin?tab=model-prices`：录入已核对的成本价；管理员设定加价倍率并发布售价。跨币种不自动假设汇率。价格需有真实来源与有效版本。
3. `/admin?tab=model-costs` 与 `model-risk`：发布成本总上限、单次/周期/并发限额及放行名单。`docs/operations.md §11` 是参数约束说明，不是已执行的配置。
4. `/admin?tab=model-provider`：填写官方已返回的模型 `deepseek-flash`、证据引用、价格版本、上下文/输出上限与有效期；通过受治理密钥字段密封保存。默认未完成调用均不收费，管理员可按类型修改并发布。不得将真实密钥放入 runtime.env、Git、聊天或日志。
5. 放行隔离验收付款方，完成真实模型、工具、账单、停止与恢复验证后再扩大开放。钱包或个人限额不足时必须拒绝发送。

## 真实充值尚缺

收款公司法定名称与注册地区、可用 Stripe 商户、获准渠道/币种、正式密钥和 webhook 验证。密钥只经后台受治理字段输入。真实回调地址是 `https://accounts.synwevo.com/api/auth/payments/stripe/webhook`；事件列表与验收见 `docs/platform/wallet-payments.md`。

`/admin?tab=payments` 未正确配置之前，充值返回 unavailable，不创建假订单；不能用测试钱包赠送证明真实充值成功。组织付款沿现有 `org.billing.manage` 与成员支出授权。

DeepSeek 针对 API 数据训练与保留期限的书面答复仍未取得；未代发外部邮件、未承诺零训练/零保留。

恢复：在本工作树读取 MASTER_PROMPT.md、STATE.md、BACKLOG.md、EVIDENCE.md，核对单协调者锁与真实 Git/CI/服务器版本，从最新具体断点继续，勿重复已完成模型运行、赠送或充值。
