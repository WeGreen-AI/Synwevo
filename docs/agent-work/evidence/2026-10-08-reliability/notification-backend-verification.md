# 通知恢复后端恢复执行报告（2026-10-08）

已完成本任务原边界内收尾。工作目录：`/Users/lihiko/repo/wegreen/synwevo/.claude/worktrees/agent-billing`。
本报告依据恢复后的实际运行；暂停前的 59 pass / 1 fail 和历史失败 probe 均不作为最终证据。
用户提供的版本背景为本地 HEAD e577fc9、远端 7964fd9；本任务未联网核验远端或线上状态。

## 写集与修复

- `services/identity/synwevo_identity/admin_notification_recovery.py`：安全元数据分页、SignedPreview 预览/执行、锁内再次鉴权、事务内审计和 admin_operations durable 幂等回执；恢复阶段重新审查后无需修改。
- `services/identity/synwevo_identity/admin_routes.py`：仅两行有限 import/include 注册；恢复阶段未追加修改。
- `services/identity/tests/test_admin_notification_recovery.py`：修正 push-only 夹具，在隔离数据库中删除对应 email delivery、向独立 push_queue 插入标明用途的合成失败行；没有修改 email-only schema 约束。原有断言全部保留，另加旧 email 预览执行被拒绝、push 行不变、无成功审计/操作回执及无私有内容泄露的断言。

未修改 root conftest、其他测试文件、core、迁移、前端、共享 docs/状态或 Git；未提交、未推送、未部署。测试数据及验证产物仅在 /private/tmp。

## 实际 backend/core 契约审查

- GET `/api/admin/notification-recovery` 使用 platform.mail.view，复用 email_recovery_page：limit 默认 25、范围 1..100；offset/snapshot 有界；snapshot 基于 notifications.seq；q 仅查通知 ID/用户 ID；仅 email failed。返回 items/total/limit/offset/snapshot/next_offset。高水位排除后来插入，不是冻结可变状态的快照。
- item 精确白名单为 notification_id/user_id/category/status/attempts/updated_at/error_code/recoverable/blocked_reason，不含原始错误、正文、params、业务 target 或邮箱。
- POST `/{nid}/preview` 接受空体或空对象，使用 platform.mail.manage；响应 action/scope/risk/target（仅安全元数据）/impact/expires_at/preview_token。版本只在签名绑定内使用，不加入列表或审计。绑定 actor/action/scope/origin/notification_id/version/expiry。
- POST `/{nid}/execute` 只接受 `{preview_token, reason, idempotency_key}`；不接受 preview 别名。reason 去首尾空白后 8..500 字符；操作键为 16..128 位字母、数字、下划线或连字符。
- execute 使用 platform.mail.manage + dangerous=True，并在 writer lock 内再次核验当前身份、权限、会话和重认证；成功幂等回执的读取也在再次鉴权之后。同 actor/key 的不同规范请求返回 idempotency_conflict。
- core 仍是唯一恢复实现：failed/email、attempts>=5、mail_id NULL、精确 notification_enqueue_failed、atomic-v1、无对应通知 outbox；版本绑定调度字段、mail_id、原错误哈希及 outbox 存在谓词。历史 NULL provenance 不因本后端变为可恢复。
- 后端持有 Store.tx 时调用 recover_email_enqueue，状态变化、成功审计和 durable receipt 一起提交；失败后的拒绝/错误审计仅记录稳定错误码等治理信息。累计 attempts、通知身份及旧错误码保留，过时 defer_reason/policy_revision 清除。
- core missing / not-recoverable 错误直接对应 notification_delivery_missing / notification_not_recoverable；签名过期、无效、旧版本分别为 admin_preview_expired / admin_preview_invalid / admin_preview_stale。core 的 notification_delivery_changed 会原样传播。
- execute 响应直接合并 core receipt 与 ok/operation_id/state=succeeded，状态是 pending、recovery_scheduled=true，只表示恢复排队，不表示邮件投递成功。后端不调用 SMTP、push 或 mail_outbox 重发。

## 恢复后实际验证

使用用户指定 Python 3.12 与 PYTHONPATH，PYTHONDONTWRITEBYTECODE=1，禁用 pytest cache；真实 identity 工厂 start_jobs=False，合成身份/数据、无网络传输。

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:packages/core:packages/auth-client/python:services/identity:apps/docs:apps/portal:apps/messages:apps/suite:infra/server /private/tmp/claude-501/-Users-lihiko-repo-wegreen-synwevo/80146fec-e8b8-43eb-aafb-6b062986dc5d/scratchpad/venv312/bin/python -m pytest services/identity/tests/test_admin_notification_recovery.py services/identity/tests/test_admin_operations.py services/identity/tests/test_notification_enqueue_recovery.py services/identity/tests/test_notification_dispatch_atomic.py -o addopts='--import-mode=importlib' -q -p no:cacheprovider --basetemp=/private/tmp/synwevo-admin-notification-recovery-resumed-tests --junitxml=/private/tmp/synwevo-notification-backend-resumed-20261008.junit.xml
```

结果：**120 passed，0 failed，0 skipped，1 warning，20.88s，退出码 0**。

| 测试文件 | 通过 |
| --- | ---: |
| test_admin_notification_recovery.py | 60 |
| test_admin_operations.py | 28 |
| test_notification_enqueue_recovery.py | 12 |
| test_notification_dispatch_atomic.py | 20 |

JUnit 计数已读取核对：`/private/tmp/synwevo-notification-backend-resumed-20261008.junit.xml`。
唯一 warning 是 Starlette TestClient 引用 AnyIO BlockingPortal 弃用别名；没有压制警告。

六项机械检查均以指定 Python、PYTHONDONTWRITEBYTECODE=1 执行并返回 0：

- scripts/check_boundaries.py：803 Python、420 前端文件，11 模块边界、8 包依赖通过。
- scripts/check_brand.py：1242 处全部归类。
- scripts/check_design.py：369 前端文件通过。
- scripts/check_component_names.py：通过，4 项既有例外。
- scripts/brand_assets.py --check：22 个文件匹配。
- scripts/app_icon_assets.py --check：12 原图、36 衍生文件通过。

三个本任务源码文件还通过了只读 AST 解析。测试及四个曾返回 session ID 的进程均已确认正常退出，没有遗留本任务测试/检查进程。

## 内容指纹

| 文件 | SHA-256 |
| --- | --- |
| admin_notification_recovery.py | fdaf9f49b7b9e39cbf139d2073d1a31c3bff9f94d268041bbfe0c53465422233 |
| admin_routes.py | 0bc09ad0838cb82920bc9956775e14eb75d2f4fb458a39edb2bb0edfeb459079 |
| test_admin_notification_recovery.py | 2e8b9bff35dbe50efb98363f24f6fc83da3ca8a1fe8e6bac6f49d944e5030b66 |
| core/notifications.py（只读） | 18047ec9889d9b40a26482f3e6731facd0d7d039fcf1b53cc52b58c3085bd9a6 |
| core/store.py（只读） | d89ecab107d25dd4212e5aa489f54f9d1c04ab304ab507bbdb177f06ba131506 |

## 未测范围与交接

未运行全仓/全 identity 套件、前端 typecheck/build、浏览器或生产只读复查；未接触真实数据、SMTP、网络、真实密钥。没有本任务边界内待修复的失败项。完整发布验证、迁移/前端审阅、线上复查、提交及部署仍由协调者执行。
