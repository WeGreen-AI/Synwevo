# DeepSeek Agent 与计费：当前发布断点

最后核对：2026-10-05T20:10:16Z（香港 10月6日）。协调者 Codex `01a0f104-e57f-7092-ab67-50359f43b547`。

| 项目 | 当前事实 |
|---|---|
| 实施工作树 | `.claude/worktrees/agent-billing`；分支 `claude/agent-billing`；产品源提交 `961acbf3ad3e363486261072552c5d91a7024c32` 已推送远端main；文档/证据单独提交，不触发重复CI/CD；产品源版本保持961acbf |
| 主检出 | 主检出及图标等其他未提交工作完整保留。主检出STATE仅放恢复指针；不在主检出混合实现/提交 |
| CI | [Actions37362720332](https://github.com/WeGreen-AI/Synwevo/actions/runs/37362720332) verify111940913669 **success**，34m14s；镜像5130通过/5跳过，前端496、infra11及SDK/守卫/类型/构建通过 |
| 发布 | deploy111952838389 于20:10:16Z被平台取消，runner为空、steps=[]；官方注释：多次尝试仍未获得hosted runner。整条run为failure，verify仍success。无人工环境审批。外部Actions runner分配故障未恢复；没有执行部署、没有盲目重跑或手工旁路 |
| 生产 | 20:08:49Z只读核对仍运行 `a4b42c8da0cee86677cc7d6409e8eaff57543b2b`；新版本尚未确认部署，不能把verify通过写成上线完成 |
| 本轮修复 | 并发赠送/支付/拒付；真实内部产物链接；Forms列表读后写的来源检查；三语言手机聊天布局；生成契约同步；CI完整三阶段成功后才回收原生tmp_path，保留失败现场与全部门槛 |
| 真实验收 | 官方密钥已验证可用；隔离真实Agent16次模型调用/16笔结算，另4次适配器调用（含思考开/关与原生重放）。跨空间owner/viewer读取、只读空间写入403、具体确认后真实新建表单、刷新不重复，证据见目录 |
| 运行环境 | 隔离库 `/private/tmp/synwevo-agent-live-20261006/data`；Accounts37770与Portal37769已正常停止；隔离钱包不是实际付款。没有向客户发送通知 |
| 配置 | 主机runtime.env已预置DeepSeek/live/CNY(scale6)、欢迎额度100分=每个已验证账户/邮箱一次CNY1，沿用防重复及每日200次总量。备份见EVIDENCE；未手工重启，随CD加载。`agent`仍未加入应用名单，正式提供方密钥/售价/成本策略仍需现有管理员治理门禁 |
| 下一具体动作 | 先确认GitHub Actions执行器故障已恢复，再核对当前run及生产current.json；按下述命令重跑原部署job，保留原SHA已通过验证。若部署成功，执行下述只读上线核对并记录镜像/备份/健康；若失败，读取该作业日志修根因。不得重新执行已完成模型、赠送或创建表单 |

## 恢复与证据

1. 阅读本树MASTER_PROMPT、STATE、BACKLOG、EVIDENCE；核对主检出 `.coordinator.lock` 的真实持有者活动，不以锁龄强夺。
2. `gh run view 37362720332 --repo WeGreen-AI/Synwevo`；确认故障恢复后运行 `gh run rerun 37362720332 --repo WeGreen-AI/Synwevo --job 111952838389` 只恢复该部署job；只请求恢复deploy，由GitHub处理其依赖；不另起整条workflow，不改源SHA。
3. 部署完成后运行准备好的 `/private/tmp/synwevo-post-deploy-check.py`（通过 `ssh vultr_tokyo python3 -` 输入；预期SHA已固定961acbf），与 `/private/tmp/synwevo-public-release-check.py`。前者核对四应用镜像标签/ID、网关健康、备份、billing schema与选定非秘密配置；后者核对五域readyz/静态资产/匿名边界/OIDC跳转。尚未执行的新版本验证不得写成通过。
4. `docs/agent-work/evidence/2026-10-06-deepseek/` 保存真实模型与界面脱敏证据；`ci-verification.json` 绑定准确提交。原始CI日志 `/private/tmp/synwevo-ci-961acbf-verify.log`。

## 剩余外部条件

- 正式平台管理员以真实密码或Passkey完成admin重认证，在价格/成本/风险/提供方页面发布配置。不能通过造会话或直接写治理表替代。密钥文件已可用，不是“缺DeepSeek密钥”。具体入口与步骤见 DEEPSEEK_ENABLEMENT.md。
- 真实充值仍缺收款公司法定名称、注册地区、可用Stripe商户配置及渠道资格；需真实回调/小额受控交易验收。不得用隔离赠送证明收款成功。
- 已按用户免费“1块”解释为每个已验证账户/邮箱一次CNY1，不再将币种解释作为发布阻塞；正式售价和加价由管理员发布。
- API数据训练/保留的提供方书面答复尚未取得；询证草稿已准备，未代发邮件、未承诺零保留。
- 生产已登录浏览器验收尚未完成；本机IAB正式域名访问曾被客户端拦截，不以公开健康检查冒充完整业务验收。
- 减少动态效果尚未浏览器实测；真实上游中断收费/价窗切换与正式支付仍是单独验收项，不能由fixture结果替代。
- 六应用整体A–E矩阵仍保留在BACKLOG；本轮DeepSeek代码验证不能代表全部规格达标。

## 协调与调度

本轮因外部执行器故障及正式商业/管理员配置条件保存断点；停止本地重复轮询，记录被平台取消的部署job及恢复命令。收尾时仅释放本任务持有的主检出锁。旧Claude任务80146fec的循环d90b5952因其配额耗尽不执行，**本任务未取消该循环，也未创建新循环**。GitHub原部署job已因无法分配runner被平台取消，**不会自动继续**；故障恢复后按记录恢复该job，先查当前状态与生产版本，避免重复副作用。
