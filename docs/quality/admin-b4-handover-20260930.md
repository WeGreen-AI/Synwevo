# B4：最高管理员主动接任（2026-09-30）

状态：本轮已收尾，功能提交`6cef796`、测试修复`ff715a1`/`496c1e2`保存在本地；未推送、未部署。用户要求完成本轮后停止 goal；本轮不开始注册策略或其他新模块，一分钟自动跟进已停用。整体管理员平台仍未交付完毕。

## 实际交付

现有最高管理员在账户的平台权限中创建24小时接任提议。签名预览绑定本人、原授予ID、目标、源站、版本与有效期；创建需要近期管理认证与理由。提议不产生权限。指定接任者在账户页用实际Passkey UV接受，产生真实最高授权；进入后台，再用独立的专用UV确认就绪，才计入最后可用最高管理员。旧角色的撤销是后续独立操作。

旧普通最高角色授予和兼容 `platform-role: admin` 路由均拒绝绕过接受。普通委派角色保持原路径。新接任者未就绪时，唯一旧管理员的撤权、停用、自删和唯一验证方式移除仍受保护。通用admin证明、另一提议和另一阶段的证明均不能替代当前专用证明；两次专用成功不更新普通 `reauth_at/reauth_scope`，不延长会话。

成功证明、grant、角色镜像、因素记录、操作结果与审计同事务；挑战先单独消费，失败不复活。相同操作重试返回已保存结果，但当前相关授权已撤销则拒绝。接受前检查最初发起grant，撤销后重授不能复活旧提议；接受完成后就绪不永久依赖原发起者。恢复（含无日志恢复）作废所有pending提议和挑战，已接受授权沿用精确grant撤销日志。

界面包括发起预览、本人接受/拒绝、管理员取消、分页历史、失效状态、就绪确认、持久操作回执与独立撤权入口，简繁英同步。设备验证未完成时可取消，取消不提交接受；提交结果未知时保留同一请求和幂等编号重试。真实验收发现就绪成功后名册和提议列表仍显示旧状态，已补父级数据刷新，保持成功回执；新增浏览器断言证明修复。

## 数据与兼容

唯一Store增加 `platform_super_admin_offers`，`platform_role_grants.offer_id` 和 `reauth_challenges.context_kind/context_id`，含pending/授予唯一性索引。沿用当前schema27的幂等增量创建/加列机制，不重建账户、数据库或密钥。旧授予的offer关联为NULL，保留当前可用性规则；来源明确为既有/迁移/主机，不能当成本批的接任证明。

增量结构不代表可以安全回退治理语义：旧代码不理解专用证明、就绪计数及最高即时授予禁令。完整应急恢复、全局管理员强制MFA，以及无撤销日志时的人类授权核对门禁均仍未完成。操作步骤和恢复约束见 [使用说明](../platform/admin-operations.md) 与 [接任协议](../platform/admin-handover.md)。

## 验证证据

所有账户均为明确标记的隔离fixture，未读生产数据、授予真实身份、发送真实邮件或修改外部配置。

| 验证 | 本轮结果与范围 |
|---|---|
| 真实浏览器 | 12项业务证据通过；3语言 × 1440/390/320共9组键盘与布局检查通过；23张截图；`page_errors=[]` |
| 证明隔离和最后管理员 | 浏览器实际登记虚拟CTAP2 UV密钥；真实签名接受、实际进入后台、真实退出后新会话再次专用证明；确认通用admin UV不能代替两种专用证明；未ready撤旧root被拒；ready后仍需独立admin UV才能撤权，旧root的独立浏览器随后404 |
| 前端 | 账户19项流程检查及23项Node契约测试通过；拒绝契约覆盖账户/管理两个模块图；根目录四前端typecheck与production build通过 |
| 静态 | 边界、品牌、设计、组件名称、Logo五项检查通过；无新增豁免；diff whitespace检查通过 |
| 后端接任与关联 | 当前接任专用38项通过（2条第三方警告，19.73秒），包含失败审计绑定提议对象的最后增量；此前关联窄组184项加独立补充1项通过。各组有重叠，不累加 |
| identity + integration全套 | 1707 passed、5 failed、163 warnings，646.71秒，自然结束；包含B4主实现，最后纯失败审计关联增量由上述38项覆盖。5项均为下述测试假设，修复后分别窄验，不冒称此原始全套通过 |
| 冻结完整容器 | 提交`b348fb6`默认四套件自然结束：3091通过/4失败/5跳过/236警告，2350.26秒，无OOM。4失败为同一工厂测试假设，后续496c1e2窄验8项通过。另有一个未处理的Timer关闭竞态警告；**不含B4和后续测试修复**，见[容器记录](admin-runtime-memory-20260930.md) |

### 长回归发现的两类测试假设

- `test_gate_api_errors.py` 的精确预算断言以真实时间连续发三次请求，却要求工作空间额度始终为1197。实际1200/min的令牌桶每50ms合法补充一格，本轮返回1198。提交`ff715a1`仅给这一用例固定`api_quotas`模块的私有时间绑定，保留全局时间、生产限流和所有原断言；整文件13项通过（4.05秒），日志`/private/tmp/b4-gate-clock-fixed.log`。
- 提交`496c1e2`修复四个工厂生命周期用例：本次API/Store/bridge弱引用都已回收，失败是要求进程级`workers.live()==[]`。该登记表按契约包含仍被其他应用引用的已停止worker。本轮只修测试：对本次全部登记worker逐一检查弱引用归零，登记表不增加前序集合之外的对象，实际线程全部退出；增加前序已停止但仍被持有的工厂场景，最后放下前序工厂也必须完全回收。8个矩阵节点通过，不修改产品registry、清理缓存或放宽原弱引用/waker断言。

修复后没有重新启动整套identity+integration或另一轮完整容器，不用窄验结果替代完整门禁；冻结容器结果单独记录。Timer关闭竞态尚未处理，完整发布门禁未通过。本轮按用户要求结束，不能据此宣称平台全部交付。

浏览器脚本：`services/identity/tests/browser_admin_handover_review.py`。可复跑命令（先完成锁定安装及账户构建）：

```sh
.venv/bin/python -m services.identity.tests.browser_admin_handover_review
```

浏览器报告及截图位于 `output/playwright/admin-handover-review/`，报告为 `report.json`。重点图：`recipient-ready-two-usable-administrators.png`、`old-root-protected-before-readiness.png`、`old-root-revoked-after-separate-admin-proof.png`、`handover-accept-preview-en-320.png`、`handover-recipient-zh-Hant-390.png`。根代理独立目视复核就绪状态、英文320弹窗和繁中390接任页。虚拟认证器是测试设备；不作为实体密钥、生物识别、WebKit、真机或邮件送达证明。

接任后端日志：`/private/tmp/b4-handover-frozen.log`、`b4-backend-regression.log`、`b4-identity-integration-full.log`。

本地前端日志：`/private/tmp/synwevo-admin-handover-frontend-test.log`、`synwevo-admin-handover-typecheck.log`、`synwevo-admin-handover-all-build.log`、`synwevo-admin-handover-guards.log`。账户构建及浏览器服务均正常退出；结果文件不保存凭据响应、会话秘密或生产身份。

## 发布与停止边界

本轮仅在隔离分支 `codex/admin-platform` 收尾。修复后全量门禁尚未重验，且现有远端Actions账单阻塞未解除；不绕过CI/CD、不合入或发布到生产。注册策略设计仅保留为未实施文档；完整业务治理、应急恢复、无日志恢复安全门禁和整体正式验收仍在覆盖表中，不标记完成。
