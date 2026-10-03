# 业务应用、Admin/API 与个人 Agent：状态与断点

最后检查：2026-10-03T12:00Z。协调任务 Claude `df1f0b15-17ae-4370-a6b0-aacd082144be`（/loop f5cb7ce9，取代 9d5641b6）；统一目标仍执行，无递归loop。

| 项 | 当前事实 |
|---|---|
| 锁 | 主检出docs/agent-work/.coordinator.lock由df1f0b15持有，每轮更新heartbeat。2026-10-03T04:09Z自Codex `01a0f104…`接管：其goal paused、turn被用户中断（2026-10-02T15:05Z），服务15:10Z退出，此后无事件 |
| 实施树 | .claude/worktrees/business-apps，feat/business-apps；remote af68968（WP9事件层+traffic+CODE_REVIEW）；本地另有 ab69741 流程结果语义、0e95fcc Message排队发送、d2e1f15 数据集→文档输入（未推送） |
| PR | 草稿PR #1，https://github.com/WeGreen-AI/Synwevo/pull/1；未合并/部署本分支 |
| 主检出 | main@2dd03c7；其他图标任务改动完整保留，不触碰 |
| CI | cf1bb32 Actions37105534440 success；af68968 Actions37119711384 verify 运行中（11:27Z 起） |
| 当前包 | B1 跨应用链路：引擎 partial/failed/cancelled 语义、portal 角色 Message 排队投递与收据结算、DataPrep 数据集物化为 Docs 输入均已本地验证并提交；B1 七应用端到端测试由子代理编写中 |
| 并行 | local_561a6cda：签名 Webhook（claude/suite-machine-webhooks，叠在 af68968），CR-035 已修复于 1dba69b/47daf54；只本地提交 |
| 下一动作 | 完成 B1 端到端测试与浏览器验收；全量门槛后推送 ab69741..；合并 Webhook 栈；随后跨空间 Agent（C1–C4） |

## 已验证与边界

- 持续同意：本人对一次已审核报价设定1–1000次、明确到期（≤366天）或直到撤销；固定route/价格/范围/每次预算/调用/期限；basis quote同事务撤销。每次人工发送原子签发并绑定普通quote；每次派发重检同意状态与钱包/周期/成本门槛。记录与绑定由SQLite触发器保持不可变。agent/flow/automation/system/api均拒绝（仅UI），机器入口须单独评审。
- 个人预算（d3cba3c，已在6052c13）：日/周/月上限、原子准入、三语言设置界面已验收；周期上限不等于持续付款同意。
- 正式DeepSeek/收费/支付退款未启用；本机协议、fixture路由和资金测试不代表真实链路。

## 所有权、环境与恢复

- 隔离验收：accounts 33770（/private/tmp/synwevo-payer-ui.py）与portal 33769（scratchpad/consent-ui.py：fixture路由、MockTransport阻断网络、空成本策略即关闭准入、无资金），数据仅scratchpad/live/data，SYNWEVO_TEST_INSTANCE=1；验收后已停止。31765/32765不触碰。
- 测试包装/private/tmp/synwevo-suite-test.sh使用本树包和根venv3.14.5。
- detached /private/tmp/synwevo-price-validation及stash513202cb1e188d613472d1506d755f238d9d14ed继续保留。

## 外部条件及余项

- 商户/运营主体/支付渠道/币种和商业政策待已有问题答复，不重复问、不编造金额或资金。
- 真实DeepSeek此前仅models/balance GET200，未推理/收费；真实model/tool/产物/计量与支付生产小额测试仍未验。
- CI：DECISIONS 2026-10-02已规定不无限加时；须找到单测固定成本（现约1s/项，早期0.35s）根因，保留全部测试与断言。
- 余项依MASTER_PROMPT/BACKLOG：RPM/TPM及公平队列、上游余额/成本治理、支付退款、完整跨应用B1/跨空间Agent、API双SDK（WP9并行中）与全部应用验收。
