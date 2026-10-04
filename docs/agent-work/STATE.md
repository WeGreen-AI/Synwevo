# 业务应用、Admin/API 与个人 Agent：状态与断点

最后检查：2026-10-04T13:40Z。协调任务 Claude `80146fec-e8b8-43eb-aafb-6b062986dc5d`（/loop d90b5952）；2026-10-03T15:01Z 自 `df1f0b15…` 接管：用户于 14:00Z 中断其回合并取消其循环 f5cb7ce9，原并行会话 `12134ed7…`（Admin）与 `7bc70746…`（Webhook）同时被中断；三者均已书面交接并确认不再写入。

| 项 | 当前事实 |
|---|---|
| 锁 | 主检出 docs/agent-work/.coordinator.lock 由 80146fec 持有（记录了接管证据），每轮更新 heartbeat |
| 实施树 | feat/business-apps@a47b148：第三至第六轮复核修复全部完成（CR-051..053），全量 4788 通过 2 跳过；业务应用、Agent、计费在生产默认关闭（SYNWEVO_SUITE_APPS 未设、Agent 需显式模型与密钥、计费环境未设） |
| PR | 草稿 PR #1：7e89bda CI 通过；a47b148 经 main 的 Verify and deploy 验证（快进合入） |
| 主检出 | main@7d690f1（7ae3527 部署 + 部署记录）；主检出工作树的图标任务等其他会话改动完整保留，不触碰 |
| CI / 部署 | **生产已更新到 7ae3527**：Actions 37185330301（07:17→07:56 UTC）验证并部署 Admin 缺陷修复 D5（部分）/D6/D7 第一步/D9/D12/D13 及两轮对抗复核发现的后续修复；五个正式主机 /readyz 200，四个角色同为 synwevo:7ae35276（只读核对），备份 20261004T075543Z；记录 docs/quality/deployment.md（main@7d690f1，仅文档未触发 CI） |
| 本机门槛（admin-d9 7ae3527，2026-10-04T07:00Z） | testpaths 2996 passed / 2 skipped；infra unittest 8；npm test、typecheck、build、五守卫（c9896b6，其后无前端改动）；拒绝文案目录 7/7；SDK 与 main 相同未重跑 |
| 当前包 | ① 第三轮复核修复（feat/business-apps，见上）；② R3-FE-04：事件唤醒按 poll_s 限频由协调者修（他人事件耗尽运行预算是缺陷）；预算规则本身（状态检查计入 max_calls、预算耗尽跳过 on_error、默认 7 天等待撑不满）为所有者决定；③ Admin 余项：D3/D4/D10/D11/D14/D15/D18 与低项，见 CODE_REVIEW CR-050 |
| 并行 | 工作流 wf_a736bbb9-19a（第四轮复核，只读，四区域查找+核实）；business-apps 无其他写入者；r3-webhooks 已合入 |
| 下一动作 | 快进 main 到 a47b148 → Verify and deploy → 五主机 /readyz 与四角色镜像核对 → 记录 deployment.md；之后：业务应用逐个开放前的验收（SYNWEVO_SUITE_APPS 属主机配置，先说明再做）；所有者决定：D3、D5 其余、D7 第二步、R3-FE-04 预算规则、R3-WH-2/R4-webhooks-merge-1/2 解析池设计、ClamAV 主机步骤 |

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
