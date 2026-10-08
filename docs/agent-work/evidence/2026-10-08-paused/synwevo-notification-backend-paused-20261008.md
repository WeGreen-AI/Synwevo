# 通知恢复后端暂停断点（2026-10-08）

用户明确要求暂停；保留当前文件，未提交、未推送。工作目录：
`/Users/lihiko/repo/wegreen/synwevo/.claude/worktrees/agent-billing`。

## 已改文件
- `services/identity/synwevo_identity/admin_notification_recovery.py`：新增 metadata list、SignedPreview preview/execute、锁内鉴权、事务内审计与 durable 幂等回执。
- `services/identity/synwevo_identity/admin_routes.py`：仅增加恢复路由的 import/include 注册。
- `services/identity/tests/test_admin_notification_recovery.py`：新增隔离身份应用测试。

最终接口契约使用 `preview_token`；preview 返回 action/scope/risk/target（core 安全元数据）/impact/expires_at/preview_token；execute 接收 preview_token/reason/idempotency_key，直接合并 core receipt 与 ok/operation_id/state=succeeded。仅表示恢复排队。

## 已完成验证
- 已读指定根 AGENTS、CLAUDE、monorepo、identity AGENTS 和 REVIEW_STOPPED；历史失败 probe 未计为证据。
- 已用用户指定 Python 3.12/PYTHONPATH 运行一次本新增测试文件，关闭 pytest cache 和 bytecode，测试数据仅在 `/private/tmp/synwevo-admin-notification-recovery-tests`。
- 结果：59 项通过、1 项失败，无跳过；另有 Starlette/AnyIO 弃用警告。权限、重认证、actor/origin/expiry、stale version、同键变参、并发、审计/回执失败回滚等已通过。
- 失败：`test_push_only_or_missing_delivery_is_not_available` 把 email delivery 的 channel 改为 push，违反 schema 的 email-only CHECK；属于新测试夹具错误，尚未修正。源码已确认 push 使用独立 `push_queue` 表。
- 未运行机械检查、其他测试套件、构建或部署；未访问真实数据、网络、浏览器或真实密钥。未修改 Git、共享状态、core、迁移或前端。

## 进程及终止情况
- 唯一启动的测试进程为 exec session `66297`，已由 write_stdin 返回退出码 1，输出已到 100%；中断发生时无本任务已知运行中的测试/构建进程，无需终止。
- 暂停时补充尝试仅匹配本测试命令的 ps 检查，被沙箱拒绝（operation not permitted）。未提权、未杀其他任务进程，也未重新运行测试。

## 下一动作（仅用户恢复后）
1. 把上述 push-only 测试改成独立 push_queue 夹具并移除对应 email delivery，验证列表/预览排除它；不改 schema/core。
2. 用相同指定环境重跑新增定向测试，检查有限写集及必要边界检查，按实际结果报告。
3. 协调者继续独占 core 专项回归、迁移、前端、提交与部署；本任务不提交不推送。
