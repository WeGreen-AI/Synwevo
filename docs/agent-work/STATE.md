# 业务应用、Admin/API 与个人 Agent：状态与断点

最后检查：2026-10-04T01:15Z。协调任务 Claude `80146fec-e8b8-43eb-aafb-6b062986dc5d`（/loop d90b5952）；2026-10-03T15:01Z 自 `df1f0b15…` 接管：用户于 14:00Z 中断其回合并取消其循环 f5cb7ce9，原并行会话 `12134ed7…`（Admin）与 `7bc70746…`（Webhook）同时被中断；三者均已书面交接并确认不再写入。

| 项 | 当前事实 |
|---|---|
| 锁 | 主检出 docs/agent-work/.coordinator.lock 由 80146fec 持有（记录了接管证据），每轮更新 heartbeat |
| 实施树 | .claude/worktrees/business-apps，feat/business-apps；origin 614b0cb（CR-047 第一、二轮修复全部推送并合入 main@9bf71b9）；本地另有 078a2a1（并入平台运行页 claude/admin-governance 3dc06d6）与记录更新，待 Webhook 分支（claude/suite-machine-webhooks）全量回归后一并合入、推送 |
| PR | 草稿 PR #1（https://github.com/WeGreen-AI/Synwevo/pull/1）：曾因与 main 冲突导致 pull_request 不触发；合并 main 后恢复 MERGEABLE，614b0cb 的 CI（Actions 37173429956）进行中 |
| 主检出 | main@2dd03c7；图标任务等其他会话的改动完整保留，不触碰 |
| CI / 部署 | **生产已更新**：Actions 37171551868 验证并部署 b8cb31a（2026-10-04 03:09 UTC）；五个正式主机 /readyz 200，四个角色同为 synwevo:b8cb31a8（只读核对）；记录 docs/quality/deployment.md（main@9bf71b9，仅文档未触发 CI）。阻断原因：Actions 计费（已恢复）与两处潜伏测试缺陷（GCRA 计时断言、运行时 Python 决定的 413/416 描述），均已在 main 修复 |
| 本机门槛（提交前同一树，2026-10-04T00:25–00:48Z） | 五套件 4572 passed / 2 skipped / 0 failed（docs 1488+2 skipped、identity 1206、messages 63、suite 717、integration 1098）；五守卫、typecheck、build、npm test（6 个工作区 360 项）、infra unittest 8、Python SDK 35 均通过 |
| 当前包 | ① data_flow：第二轮复核 R2-F1..F8 全部修复（无高；中 4 项）；② 平台运行页：独立核查 AR-1/AR-2（中）已修复并入；③ Webhook：管理侧修复与补审 WH-1..WH-6 已修复（worktree suite-machine-api，全量回归中）；④ 待做：浏览器验收（Settings 固定默认政策、Automations 拒绝原因、平台运行页停滞/历史标签）、第三轮数据流复核或协调者自审、CR-048 其余（DF-B2/B3/B4 流程引擎健壮性）、D3–D20 |
| 并行 | 只读：data_flow 审查×2、admin 运行页审查×1（会话重启后已续跑）；工作流 wf_f4d87c36-c38（Webhook：交付侧修复→管理侧修复→授权/数据暴露与核心影响面两路审查+对抗核实，唯一写入者，仅 suite-machine-api；step 2 已本地提交 930a954）；工作流 wf_a9743a28-ba8（只读复核 admin-coverage D3–D20 并给出最小修复方案，6 组） |
| 下一动作 | 本地 feat/business-apps 已合入平台运行页、Webhook（WH-1..6）、流程健壮性（DF-B4/DF-B3），全量门槛在跑（scratchpad/gate-py.log）；门槛通过后推送。浏览器验收已做（隔离实例 scratchpad/live，36769/36770）：平台运行页停滞标记、历史标签、英文与 375px；Automations 拒绝原因；Settings 固定默认政策（revision 1）。待：门槛结果、推送、PR CI、第三轮复核、D3–D20 |

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
