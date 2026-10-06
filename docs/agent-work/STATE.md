# DeepSeek Agent 与计费：当前发布断点

最后核对：2026-10-06T01:41:40Z（香港09:41）。协调者 Codex `01a0f104-e57f-7092-ab67-50359f43b547`。

| 项目 | 当前事实 |
|---|---|
| 实施工作树 | `.claude/worktrees/agent-billing`；分支 `claude/agent-billing`；产品源 `961acbf3ad3e363486261072552c5d91a7024c32` 已推送main并部署；其后仅文档/证据提交，不触发重复CI/CD |
| 主检出 | 主检出及图标等其他未提交工作完整保留；STATE仅放恢复指针 |
| CI/CD | [Actions37362720332](https://github.com/WeGreen-AI/Synwevo/actions/runs/37362720332) attempt2 **success**。deploy112064193905在01:40:51Z成功；复用10月5日已通过verify，未重复全量测试。原attempt1因未分配runner取消的记录保留在EVIDENCE |
| 验证门槛 | 准确源961acbf：镜像5130通过/5跳过；前端496、infra11及SDK/守卫/类型/构建通过。原verify111940913669，attempt2显示复用作业112064194680 |
| 生产 | 四应用运行 `synwevo:961acbf3ad3e363486261072552c5d91a7024c32`；镜像ID `sha256:1358526d4d1776dcf8b28cb115f65c0fb654fbbc2694a3ea3076278d3d8bd14e`，OCI revision一致；accounts/docs/portal/messages/gateway均healthy |
| 备份 | `/opt/synwevo/backups/20261006T014023Z` 的manifest与数据库存在，来源为前版本a4b42c8；本轮没有执行恢复，不能将存在备份写成恢复演练 |
| 上线核对 | 五域readyz/root/实际JS资源200；各角色数据库/存储/worker/扫描器就绪；工作空间登录303到Accounts的OIDC授权路径；匿名提供方与充值选项接口401。仅基础上线核对，未冒充已登录生产业务验收 |
| 本轮修复 | 并发赠送/支付/拒付；真实内部产物链接；Forms列表读后写的来源检查；三语言手机聊天布局；生成契约同步；CI完整三阶段成功后才回收原生tmp_path，保留失败现场与全部门槛 |
| 真实验收 | 官方密钥已验证可用；隔离真实Agent16次模型调用/16笔结算，另4次适配器调用。跨空间owner/viewer读取、只读空间写入403、具体确认后真实新建表单、刷新不重复，证据见目录 |
| 运行环境 | 隔离Accounts37770与Portal37769已停止；隔离钱包不是实际付款。未重放付费测试、赠送或表单创建；未向客户发送通知 |
| 配置 | CD已加载DeepSeek/live/CNY(scale6)、欢迎额度100分=每个已验证账户/邮箱一次CNY1及每日200次限制。`agent`仍未加入应用名单；生产model_provider_versions/payment_settings_versions与billing_calls/settlements均为0。正式提供方/售价/成本策略仍须现有管理员治理门禁，充值未启用 |
| 下一具体动作 | 正式平台管理员真实重认证，按DEEPSEEK_ENABLEMENT发布价格、成本/风险与提供方配置；再受控验收。真实充值另需商户配置；勿再次触发已完成部署或重放旧业务运行 |

## 恢复与证据

1. 阅读本树MASTER_PROMPT、STATE、BACKLOG、EVIDENCE；核对主检出协调者锁及真实Git/CI/服务器状态，不以锁龄强夺。
2. 本次远程部署已完成，无需再执行旧run的rerun命令。新功能发布按新的准确提交走现有门槛。
3. `evidence/2026-10-06-deepseek/release-status.json`、`production-after-deploy.json`、`public-after-deploy.json` 保存脱敏成功记录。原始部署日志仅在 `/private/tmp/synwevo-resumed-deployment.log`，无密钥进入证据。
4. `ci-verification.json` 绑定准确源提交；隔离模型/界面验收保留独立来源，不将其计为生产交易。

## 剩余外部条件

- 正式平台管理员以真实密码或Passkey完成admin重认证，在价格/成本/风险/提供方页面发布配置。不能通过造会话或直接写治理表替代。密钥文件已可用，不是“缺DeepSeek密钥”。具体入口与步骤见 DEEPSEEK_ENABLEMENT.md。
- 真实充值仍缺收款公司法定名称、注册地区、可用Stripe商户配置及渠道资格；需真实回调/小额受控交易验收。不得用隔离赠送证明收款成功。
- 已按用户免费“1块”解释为每个已验证账户/邮箱一次CNY1，不再将币种解释作为发布阻塞；正式售价和加价由管理员发布。
- API数据训练/保留的提供方书面答复尚未取得；询证草稿已准备，未代发邮件、未承诺零保留。
- 生产已登录浏览器验收尚未完成；本机IAB正式域名访问曾被客户端拦截，不以公开健康检查冒充完整业务验收。
- 减少动态效果尚未浏览器实测；真实上游中断收费/价窗切换与正式支付仍是单独验收项，不能由fixture结果替代。
- 六应用整体A–E矩阵仍保留在BACKLOG；本轮DeepSeek代码验证不能代表全部规格达标。

## 协调与调度

用户要求的远程代码部署及基础上线核对已完成；正式Agent开放/真实收款仍受以上实际条件限制。收尾保存证据、提交并推送文档，再仅释放本任务持有的锁。旧Claude任务80146fec的循环d90b5952未由本任务取消，亦未新建循环。没有遗留本地部署watch或测试服务；不得以旧的runner故障断点重复部署。
