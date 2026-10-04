# 业务应用、Admin/API 与个人 Agent：状态与断点

最后检查：2026-10-04T00:50Z。协调任务 Claude `80146fec-e8b8-43eb-aafb-6b062986dc5d`（/loop d90b5952）；2026-10-03T15:01Z 自 `df1f0b15…` 接管：用户于 14:00Z 中断其回合并取消其循环 f5cb7ce9，原并行会话 `12134ed7…`（Admin）与 `7bc70746…`（Webhook）同时被中断；三者均已书面交接并确认不再写入。

| 项 | 当前事实 |
|---|---|
| 锁 | 主检出 docs/agent-work/.coordinator.lock 由 80146fec 持有（记录了接管证据），每轮更新 heartbeat |
| 实施树 | .claude/worktrees/business-apps，feat/business-apps；HEAD = data_flow 契约（a407c2c）+ CI 测试修复（e6018ff）+ 本记录提交，按用户 2026-10-04 指令“直接推送”推送到 origin/feat/business-apps（未触碰 main，未部署） |
| PR | 草稿 PR #1，https://github.com/WeGreen-AI/Synwevo/pull/1；未合并/部署本分支 |
| 主检出 | main@2dd03c7；图标任务等其他会话的改动完整保留，不触碰 |
| CI | 67b08c4 verify 失败（偶发测试，CR-039，已由 e6018ff 修复）；新推送提交的 CI 待该 SHA 的运行结果，尚未核对 |
| 本机门槛（提交前同一树，2026-10-04T00:25–00:48Z） | 五套件 4572 passed / 2 skipped / 0 failed（docs 1488+2 skipped、identity 1206、messages 63、suite 717、integration 1098）；五守卫、typecheck、build、npm test（6 个工作区 360 项）、infra unittest 8、Python SDK 35 均通过 |
| 当前包 | ① data_flow 已提交推送；其正确性审查与三路对抗复核（wf_5138fc3a-478）仍在进行，结论到达后另行修复提交（推送先于这两项结论，属用户指令）；② Webhook 分支：工作流 wf_f4d87c36-c38 修复与补审中，完成后变基到新 HEAD 再合并；③ Admin 运行页：工作流 wf_653d06ea-6cf 修复与复验中，随后浏览器验收、变基合并；④ Admin 缺陷 D3–D20 已复核，待分组修复（D3 语义待所有者决定） |
| 并行 | 只读：data_flow 审查×2、admin 运行页审查×1（会话重启后已续跑）；工作流 wf_f4d87c36-c38（Webhook：交付侧修复→管理侧修复→授权/数据暴露与核心影响面两路审查+对抗核实，唯一写入者，仅 suite-machine-api；step 2 已本地提交 930a954）；工作流 wf_a9743a28-ba8（只读复核 admin-coverage D3–D20 并给出最小修复方案，6 组） |
| 下一动作 | 跟踪新 SHA 的 CI；处理 data_flow 复核结论；合并 Webhook 与 Admin 运行页；D3–D20 修复批次；随后 C3 同名对象澄清、C5–C10 复核、CR-045（Docs 复制/模板库纳入数据流政策） |

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
