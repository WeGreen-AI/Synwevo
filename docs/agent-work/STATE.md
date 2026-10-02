# 业务应用、Admin/API 与个人 Agent：状态与断点

最后检查：2026-10-02T11:44:53.850511+00:00。协调任务 `01a0f104-e57f-7092-ab67-50359f43b547`；统一目标仍执行，无递归loop。

| 项 | 当前事实 |
|---|---|
| 锁 | 主检出docs/agent-work/.coordinator.lock当前任务持有；仅本人更新，不按年龄夺锁 |
| 实施树 | .claude/worktrees/business-apps，feat/business-apps，HEAD d3cba3c212e20e528a9c933323df9b4008672de4；remote bff35a8；SDK/CI容量修复已本地验证，准备提交并一起推送 |
| PR | 草稿PR #1，https://github.com/WeGreen-AI/Synwevo/pull/1；未合并/部署本分支 |
| 主检出 | main@2dd03c78fa6414e044246d5e33d7a1ad4355dcfc；其他图标任务改动完整保留 |
| CI | bff35a8 Actions36997527305失败：2922通过/1失败/3跳过，2377.14s；SDK来源断言指向site-packages，非timeout；构建通过，deploy skipped |
| 当前包 | 本人日/周/月支出上限、原子Ledger准入、本人API、三语言实际设置界面、迁移与故障验收 |
| 下一动作 | 提交SDK/CI容量修复后正常推送一次、跟踪新SHA。继续持续付款授权/速率公平/真实收费与全部统一范围 |

## 已验证与边界

- 个人预算按user/env/unit跨namespace与价格累计；UTC日/周/月、原始预留周期结算、所有旧hold计入当前敞口。reserve和dispatch同writer事务检查；首次无配置拒绝，停用不取消既有结算。
- 预算配置与权限/审计/事件/收据原子；版本不可变，不能由模型、workflow或automation读写。周期上限不授予持续付款同意，仍每次run固定quote。
- 最终本机544后端通过/87.050s，无失败/错误/跳过；Python3.12隔离镜像54专项通过/12.75s（2弃用警告）；Suite62前端/typecheck/build通过（Settings-BoK2sgf4.js），五守卫670Python/343前端/1185品牌/22Logo通过。
- 最后一项前端审查修复：历史结算合计可以超过单笔int64，上限输入仍int64；usage按10000候选安全最大值校验、BigInt显示，不把合法旧账务显示成整体不可用。对应独立前端回归通过。
- 真实portal33769已正常OIDC登录，三语言/390×844/1280×720/键盘/精度拒绝/具体确认、停用、刷新与版本历史已验。测试预算最终revision2 disabled，日0/周5/月20 TEST_ONLY；未生成模型调用或资金。截图工具记录已检查，但当前IAB不支持导出文件，未声称新增仓库PNG。未验reduced-motion/完整WCAG。
- 正式DeepSeek/收费/支付退款没有启用；本机协议和资金测试不代表真实链路。完整本SHA CI未通过。

## 所有权、环境与恢复

- Hume前端、Nietzsche审查/两测试文件/Python3.12已移交并关闭；协调者拥有全部改动。Plato只读CI容量方案已移交，所有子任务已关闭；协调者独占写入。
- 自有真实测试服务：/private/tmp/synwevo-payer-ui.py，portal33769 session17742、accounts33770 session93199，start_jobs=False。仅已有Claude scratchpad/live/data隔离库，SYNWEVO_TEST_INSTANCE=1。旧portal77949已停止；其他31765/32765不触碰。
- prices.localhost:33772已有独立浏览器阻断，服务已停；没有更换源站绕过该Admin验证。当前portal为原有业务验收源站。
- 测试包装/private/tmp/synwevo-suite-test.sh使用本树包和根venv3.14.5。诊断镜像synwevo:admin-b3-verify-b348fb6（3.12）只读源、禁网/3GiB/2CPU，无真实数据；不是本提交生产镜像。
- detached /private/tmp/synwevo-price-validation及stash513202cb1e188d613472d1506d755f238d9d14ed继续保留。

## 外部条件及余项

- 商户/运营主体/支付渠道/币种和商业政策待已有问题答复，不重复问、不编造金额或资金。
- 真实DeepSeek此前仅models/balance GET200，未推理/收费；真实model/tool/产物/计量与支付生产小额测试仍未验。
- 保留CI/保护/环境门禁，无替代SSH发布。CI容量诊断GC分批只改善3.4%，不改清理；SDK来源已本地修复；基于4257项实测速率将verify预算改75min，原资源/清理/全量测试/部署门禁保留，待新完整CI。
- 余项依MASTER_PROMPT/BACKLOG：持续付款同意、RPM/TPM及公平队列、上游余额/成本治理、支付退款、完整跨应用B1/跨空间Agent、API双SDK与全部应用验收；不能因预算局部完成结束目标。
