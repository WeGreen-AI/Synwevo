# 业务应用、Admin/API 与个人 Agent：状态与断点

最后检查：2026-10-03T07:20Z。协调任务 Claude `df1f0b15-17ae-4370-a6b0-aacd082144be`（/loop 9d5641b6）；统一目标仍执行，无递归loop。

| 项 | 当前事实 |
|---|---|
| 锁 | 主检出docs/agent-work/.coordinator.lock由df1f0b15持有，每轮更新heartbeat。2026-10-03T04:09Z自Codex `01a0f104…`接管：其goal paused、turn被用户中断（2026-10-02T15:05Z），服务15:10Z退出，此后无事件 |
| 实施树 | .claude/worktrees/business-apps，feat/business-apps；HEAD=remote cf1bb32（consent 21f2cd5、CI提速00c4f54、限流修复6533c2a、WP9三层13提交） |
| PR | 草稿PR #1，https://github.com/WeGreen-AI/Synwevo/pull/1；未合并/部署本分支 |
| 主检出 | main@2dd03c7；其他图标任务改动完整保留，不触碰 |
| CI | 6533c2a Actions37103506897：verify 24min（原74），pytest 19m44/1184.54s，3583 passed/1 failed/3 skipped；唯一失败为工作簿字节跨秒的既有脆弱测试，修复3488e6d已含于cf1bb32。cf1bb32 CI运行中 |
| 当前包 | 已完成并推送：持续同意、CI固定成本根因修复、GCRA跨限额变更修复、WP9机器入口/延后工作撤权/文件路由。下一包：模型调用RPM/TPM与公平等待 |
| 并行 | local_561a6cda「Synwevo 项目交付与代码审查」：WP9第二轮审查4项修复（standing失败关闭、Tables票据按客户端、范围不泄露来源、started事件顺序）后做事件拉取claude/suite-machine-events；只本地提交，不碰agent/**、billing/** |
| 下一动作 | 跟踪cf1bb32 CI；实施模型调用RPM/TPM/在途/有界公平等待（扩展版本化成本策略、Admin三语言）；合并WP9审查修复与事件拉取 |

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
