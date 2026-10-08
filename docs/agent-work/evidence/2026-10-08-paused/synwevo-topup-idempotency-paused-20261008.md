# 充值客户端幂等修复：暂停断点（2026-10-08）

用户明确要求暂停；已停止实现和测试，保留当前文件，未提交、未推送，未接触生产、真实支付或密钥。工作目录：`/Users/lihiko/repo/wegreen/synwevo/.claude/worktrees/agent-billing`。

## 已改文件（仅部分实现，尚未验收）

- `services/identity/synwevo_identity/payments.py`：添加客户端键校验、原始请求哈希、意图查询及事务占位、当前付款权限复核和重放响应；provider 未知响应保留 creating，不再因超时自动失败；URL 返回检查终态、到期、配置关闭与凭据版本。意图与 lifecycle 同事务，账本继续使用独立事务，外部调用在事务外。
- `services/identity/synwevo_identity/payment_routes.py`：接收可选 idempotency_key，返回 201/200/202；challenge 仅用于新订单；重放不重复创建审计；options 增加 initiator_id/environment；组织订单读取也复核当前付款权限。
- `services/identity/tests/test_wallet_topups.py`：新增无键重复下单复现和丢失响应后同键恢复回归测试。

尚未修改 `TopupDialog.tsx`、消息词库或前端测试；未修改迁移、notifications、governance 或共享文档。

## 已完成验证

在修改服务端实现之前执行：

```sh
PYTHONPATH=.:packages/core:packages/auth-client/python:services/identity:apps/docs:apps/portal:apps/messages:apps/suite:infra/server /private/tmp/claude-501/-Users-lihiko-repo-wegreen-synwevo/80146fec-e8b8-43eb-aafb-6b062986dc5d/scratchpad/venv312/bin/python -m pytest services/identity/tests/test_wallet_topups.py -q -k 'legacy_requests or lost_create_response'
```

结果：复现测试通过（两次无键 POST 产生不同订单、两次隔离 provider test double 调用）；新恢复测试失败（旧路由拒绝 idempotency_key，400），命令退出码 1。这是修复前证据。修改后的代码尚未运行测试、语法检查、类型检查或构建；旧 provider failure 测试仍需随未知状态契约更新。

## 所需 DDL（协调者尚未添加）

由协调者在 `packages/core/synwevo_core/suite_schema/payments.py` 添加，我未写迁移：

```sql
CREATE TABLE IF NOT EXISTS payment_topup_intents (
    environment TEXT NOT NULL CHECK(environment IN ('test','live')),
    initiator_id TEXT NOT NULL REFERENCES users(id),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    order_id TEXT NOT NULL UNIQUE REFERENCES payment_topups(order_id),
    created_at INTEGER NOT NULL,
    PRIMARY KEY(environment,initiator_id,idempotency_key)
);
```

需禁止 UPDATE、DELETE、冲突 INSERT/REPLACE 的不可变触发器。键为 1–200 ASCII 字母/数字/下划线/连字符；哈希为 SHA256。哈希规范化原始 payer_kind/payer_id/package_id/amount_minor，不含 challenge 或变化后的套餐价格；同键异参 409。当前 keyed 路径依赖该表，迁移未到位前不能视为可运行交付。无键旧调用保持独立创建语义，不具备跨请求恢复保证。

## 进程及终止情况

本任务唯一启动的测试会话为 unified exec session `90910`，已通过 write_stdin 收到最终退出码 1，无本任务已知仍运行的测试/构建进程；未启动构建或服务，因此没有需要发送终止信号的已知进程。暂停时尝试只读进程清单，`ps` 被沙箱拒绝（operation not permitted），未扩大权限或操作其他任务进程。不能据此断言其他任务没有进程。

## 恢复后的下一动作（须用户恢复授权）

1. 协调者添加并确认以上唯一迁移；检查当前服务端差异（尤其 admission 块缩进、账本独立事务和创建期间配置/权限竞态），尚未完成全面检查。
2. 完成 UI：按 environment/initiator/payer 隔离 sessionStorage；发送前保存稳定键和原始参数，绝不存 checkout URL、challenge 或支付秘密；未知响应保留意图并锁金额，重试只恢复原订单，取得终态后显式开始新意图。
3. 补齐并发同键、异参、网络/provider 超时、撤权/配置变更、终态、个人/组织与前端恢复测试；只用隔离 test double，随后运行定向 Python 测试和必要前端检查。禁止 Git、网络、浏览器、生产及真实支付的约束仍有效。

父会话消息工具拒绝向 native ancestor 发送更新，DDL 已在本会话 commentary 同步；未通过其他通道发送。
