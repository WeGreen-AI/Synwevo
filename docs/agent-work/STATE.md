# 业务应用与个人 Agent：状态与断点

最后检查：2026-09-30 00:45 HKT（协调者会话 df1f0b15…，`/loop` 调度 9d5641b6，每分钟、会话内）

| 项 | 内容 |
|---|---|
| 协调者锁 | 主检出 `docs/agent-work/.coordinator.lock`（不进 Git）。持有者会话仍在运行时不得删除；释放只释放自己的 |
| 代码分支 | `feat/business-apps` @ d16a89b（51614b4 基础+Forms，d16a89b 模块规则），worktree `.claude/worktrees/business-apps`；基于 `main` @ 2ba1e3f。并行实现者：`feat/suite-tables|dataprep|approvals`（worktree `suite-*`，从 d16a89b 分出） |
| 进度文件 | 本目录只在主检出提交到 `main`（仅 `docs/` 的推送不触发 CI）；代码只在功能分支 |
| 正在做 | WP2 个人 Agent（协调者）；WP3/WP4/WP6 由并行实现者进行，完成后由协调者合并、浏览器验证 |
| 下一条具体动作 | 实现 `synwevo_suite/agent`：会话/消息表、提供方适配器（Anthropic Messages，未配置即显示未配置）、从注册表发现工具、确认绑定、SSE；随后 Docs/Message 适配器与流程引擎（WP7） |
| 运行环境 | 测试在 worktree 里用共享 `.venv`，必须 `PYTHONPATH` 指向 worktree 各包（含 `apps/suite`）；浏览器验证用隔离 317xx 实例（一次只能跑一个 `platform.py` 实例，先确认其他会话没有在跑） |
| 线上 | aadcb9e；本专项尚无任何发布 |

## 阻塞（外部条件）

| 阻塞 | 影响 | 解除条件 |
|---|---|---|
| GitHub Actions 额度用尽（2026-09-28 起所有运行被拒） | 任何推送都不能部署（E4、WP10） | 10 月 1 日重置，或持有人在 Billing & plans 提高支出上限 |
| 没有模型提供方密钥 | Agent 真实模型链路（§9.3 “至少一条真实模型链路”、C9 的真实部分） | 持有人提供一个提供方（如 Anthropic）API 密钥，放在服务端环境变量（不进 Git） |
| 生产 nginx/compose 属主机 | 若将来拆出独立容器或子域 | 目前按 D1 不需要 |

## 本任务启动的进程

隔离验证实例（scratchpad `live/`，`live/up.sh`，数据只在 scratchpad）：accounts 33770、portal 33769（suite 代码来自 worktree）。保留供后续浏览器验证；pid 在 `live/logs/*.pid`。
