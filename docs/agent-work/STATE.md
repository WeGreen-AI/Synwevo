# 业务应用、Admin/API 与个人 Agent：状态与断点

最后检查：2026-10-04T01:15Z。协调任务 Claude `80146fec-e8b8-43eb-aafb-6b062986dc5d`（/loop d90b5952）；2026-10-03T15:01Z 自 `df1f0b15…` 接管：用户于 14:00Z 中断其回合并取消其循环 f5cb7ce9，原并行会话 `12134ed7…`（Admin）与 `7bc70746…`（Webhook）同时被中断；三者均已书面交接并确认不再写入。

| 项 | 当前事实 |
|---|---|
| 锁 | 主检出 docs/agent-work/.coordinator.lock 由 80146fec 持有（记录了接管证据），每轮更新 heartbeat |
| 实施树 | .claude/worktrees/business-apps，feat/business-apps；HEAD = 43e7e3c（df-flows 快进合入：流程运行按“整条事件链读过的空间”问数据流政策）＋本地记录；64e9348 已推送 origin/feat/business-apps，43e7e3c 待门槛通过后推送 |
| PR | 草稿 PR #1，https://github.com/WeGreen-AI/Synwevo/pull/1；64e9348 的 CI（Actions 37166232361）通过；未合并 |
| 主检出 | main@2dd03c7；图标任务等其他会话的改动完整保留，不触碰 |
| CI / 部署 | 重跑 36688073906（4b74796）verify 1 失败 / 2865 通过：tests/integration/test_gate_api_errors.py 断言工作空间余量恰为 1197，而 GCRA 每 50 ms 回补一次，CI 容器较慢时读到 1198（计时依赖的测试缺陷，非产品缺陷；本机延迟客户端复现）。修复 37cd253（按耗时界定余量），连同 API 会话已提交且本地验证的 5 个提交（游标签名、断点上传类型、来源/下载错误契约）于 01:55Z 快进推送 main；运行 37169458599 verify 进行中，通过后既有流水线部署并检查公网健康。生产仍为 aadcb9e，直到该运行完成 |
| 本机门槛（提交前同一树，2026-10-04T00:25–00:48Z） | 五套件 4572 passed / 2 skipped / 0 failed（docs 1488+2 skipped、identity 1206、messages 63、suite 717、integration 1098）；五守卫、typecheck、build、npm test（6 个工作区 360 项）、infra unittest 8、Python SDK 35 均通过 |
| 当前包 | ① data_flow：df-flows（F1、F3–F7）已复核并合入；df-forms（F2/AGENT-DF-1 高：公开表单页向持链接者暴露草稿）与 df-agent（AGENT-DF-2..5、DFP-1..6）两个 worker 因子代理额度耗尽中断，改由协调者直接做；**复核无高/中项前 PR #1 不得合并 main**；② Webhook：投递侧 8 个提交在 claude/suite-machine-webhooks（0c36724），管理侧修复与两项审查因额度中断；③ Admin 运行页：claude/admin-governance f85cada（AR-1..10），独立验证因额度中断；④ D3–D20 待分组修复（D3 语义待所有者决定） |
| 并行 | 只读：data_flow 审查×2、admin 运行页审查×1（会话重启后已续跑）；工作流 wf_f4d87c36-c38（Webhook：交付侧修复→管理侧修复→授权/数据暴露与核心影响面两路审查+对抗核实，唯一写入者，仅 suite-machine-api；step 2 已本地提交 930a954）；工作流 wf_a9743a28-ba8（只读复核 admin-coverage D3–D20 并给出最小修复方案，6 组） |
| 下一动作 | 等 37169458599 结果：成功则核对五个正式主机 /readyz、各角色镜像并写 docs/quality/deployment.md；失败读日志修根因。并行：apps/suite 全量测试（合入后），推送 43e7e3c；随后直接修 F2（公开表单草稿暴露） |

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
