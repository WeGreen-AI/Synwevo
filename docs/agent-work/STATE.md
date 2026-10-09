# Synwevo 当前交付断点

## 当前状态：本轮发布完成，按用户要求暂停

最后核验：2026-10-09T10:51:51Z（UTC，只读元数据补验）；2026-10-08发布健康证据保持原检查时间。保存时间见本文件最后一行。

- 用户最新明确要求“做完这一轮先暂停”。本轮代码发布及基础上线核验已完成；不开始新工作包，不再自动循环。完整专项未完成，不能标为完成。
- 实施树 `.claude/worktrees/agent-billing`，分支 `claude/agent-billing`，origin `git@github.com:WeGreen-AI/Synwevo.git`。产品提交 **a41ea08a50b9047c8cf5bdd23ab982751b50d38c** 已正常推送 main，未强推；后续只有证据文档提交，不触发部署。主检出的其他工作保持原样。
- [Actions 37792532752](https://github.com/WeGreen-AI/Synwevo/actions/runs/37792532752) verify/deploy 均成功，15:21:52Z完成。完整运行镜像5453通过、5跳过、242项弃用警告；六守卫、全部前端/类型/构建、部署检查通过。跳过项及警告如实保留，没有为追绿改断言。
- 四个应用OCI revision与current.json均a41ea08；accounts/portal/docs/messages/gateway/clamav六容器healthy；五正式域readyz/JS、匿名管理及钱包边界、OIDC起跳通过。三个计量模块生产源码哈希匹配本地已验证源码。发布前备份 `/opt/synwevo/backups/20261008T152122Z`；没有宣称恢复演练。
- 上一5ffec9d的Actions37776600173也已成功，生产由25b084f→5ffec9d→a41ea08，均通过原CI/CD。一次按5ff核对时最终版本已切换a41，版本断言正确拒绝；改为实际最终SHA后复核通过。

## 已交付与保持的正式配置

- CR-068：充值原始请求持久幂等、未知结果保持核实、刷新恢复同订单、组织撤权复核。
- CR-069：仅可信atomic-v1通知入队失败可治理恢复，保留累计次数并走原dispatcher；历史或结果不明只读。
- CR-070：次数/金额上限拒绝的未预留调用保持不可重放意图，只在事务内证明无预留和上游观察后释放计量状态；终态恢复修正用量投影，不重放工具或修改资金。新预算建议6次且金额仍须本人填写确认，三语言说明次数/金额独立上限。
- Agent已于12:35:19Z按operations§11.1在已验证25b084f启用，本次发布后仍保留。应用列表为forms,tables,dataprep,approvals,extract,agent。原配置备份 `/opt/synwevo/config/runtime.env.bak-agent-20261008T123519Z`。
- 本人DeepSeek同意已核对；已有真实3步模型→Docs list/get→可信usage→两倍售价结算，见existing-live-read-call.json。没有以模型文字代替服务端记录；未完成新Forms产物/跨空间生产验收。
- **个人支出状态有变化**：用户此前要求保持暂停，但2026-10-09只读实测最新预算为v4 enabled，不能继续写成v3暂停。本轮没有更改预算或发起模型调用，未查询变更操作者/时间；后续如需调整仍走本人产品操作门槛。平台受控放行名单等配置未在本次狭义补验中重查。

## 不要重复的治理与支付配置

- 日历v1/ao_d7da658ed75f；工作日UTC01–04/06–10及10月1–7日假期，不是全年假期表。
- 成本price_85f4371761b6：CNY6，deepseek-flash，每百万峰值输入2/缓存0.04/输出8，低谷1/0.02/4。
- 成本策略v1/ao_50e7aad24bc5：每日20/每周100/每月300元，单次0.2元，在途4、未知4/0.8元；完整受控值见cost-and-sale-published.json。
- 两倍售价price_9fa1a7cbc022/ao_1bff5aa7340a，峰值4/0.08/16、低谷2/0.04/8；terms_ref synwevo-agent-cny-2x-2026-10-08。欢迎人民币1元已唯一发放，旧余额不是本轮充值或赠送。
- 提供方v1/凭据v1/ao_0da99f60b32e，requested_model/confirmed_route deepseek-flash，来源DeepSeek-V4.1-Flash；输入65536/输出8192，none/low，**有效期2026-10-15T11:00Z**。未完成调用收费选项均false。
- 支付设置v1曾密封保存并disabled；**2026-10-09最新实测为v3 enabled且credential_configured=true**。本轮只读，没有发布支付设置；旧v1的香港个人商户Hiko Sphere、卡渠道及套餐记录是历史证据，本次没有重查v3全部配置或密钥。
- 唯一Stripe回调 `we_1UOB1ZEMgc4r7oEcqTYP9tLI` 已于12:12:52Z enabled；URL `https://accounts.synwevo.com/api/auth/payments/stripe/webhook`。不要重跑prepare脚本（会停用它），不要重复创建回调或订单。未完成真实卡付款/签名唯一入账验收。
- 秘密仅密封存储及本机0600恢复文件；不输出或Git。原回调恢复文件 `/private/tmp/synwevo-stripe-live-callback-20261008.json` 含秘密，不可打印。

## 当前验收限制与明确恢复步骤

1. 充值开关：本次获用户明确授权的只读补验已核实v3 enabled；不要再按旧v1 disabled断点重复启用、发布设置或创建回调。此前浏览器读取任务外私人PDF被自动审批拒绝，本轮没有重试浏览器读取或绕过门禁。
2. 真实付款：本次只核对开关/凭据已配置标记，没有查询或执行支付，不把enabled当成真实卡支付验收。恢复后先核对是否已有付款及入账证据，再由有权付款方完成必要操作，避免重复订单或充值。
3. 生产历史用量恢复：三个指定旧失败任务均usage_state=known，各有一笔结算、一个usage.reconciled事件；step:1 settled，未预留/未尝试的step:2 released。任务仍为failed/billing_run_budget_exceeded，不能写成业务成功；本次没有手动运行恢复、重放工具或改账。详见evidence/2026-10-09-readonly-closeout/production-metadata.json。
4. 公开扩量：遵守operations.md§11.3，至少连续数日每日对账ok、账本无差异、无异常扣费后再按授权扩大。当前受控正式启用不等于所有公众已放行。
5. DeepSeek关于API训练/保留的专门书面答复未取得，草稿未发送；不宣称零训练/零保留。六应用/Admin/API完整矩阵仍有余项，详见BACKLOG；本次暂停不启动它们。

## 证据与接续

- 本轮最终证据：`docs/agent-work/evidence/2026-10-08-agent-budget/`，包含源码哈希、本地/浏览器/真实既有调用、准确CI作业和生产镜像/公共入口结果。
- Tables10000行容量实测、50页完整性、上限与混合批次在`evidence/2026-10-08-tables-capacity`；不是生产并发容量。
- 本次仅补完用户授权的只读核验并保存证据，未启动下一代码工作包或部署；开发继续按最新“做完这一轮先暂停”指令暂停。
- 协调者session `01a0f104-e57f-7092-ab67-50359f43b547` 在收尾时释放自身主树`docs/agent-work/.coordinator.lock`；其他任务锁不得删除。无本轮新建循环调度。
- 恢复指令：在实施树读取MASTER_PROMPT.md、STATE.md、BACKLOG.md、EVIDENCE.md；先核对Git/CI/生产/协调者锁，继续以上未完成门槛，复用已发布配置与证据，不重建项目、不重放副作用。

保存时间：2026-10-09T10:52:58.426984+00:00
