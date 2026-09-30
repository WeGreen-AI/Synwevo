# 工作空间目录写入（API-5b 设计）

状态：已实现，本地验收中；尚未部署。
约定来源：[`../ai/api-platform.md`](../ai/api-platform.md) §4.1「机器/服务身份」、§6.1、§7「组织与工作空间」。

## 1. 要解决的问题

API-5 交付了只读目录（`/api/workspace/v1/whoami|members|invitations`）。企业的人事/身份系统还需要：
新人入职发邀请，岗位变化改角色，离职移除成员。今天这些只能在账户页面由空间管理员逐个点。

## 2. 边界（不做什么）

* **不跳过邀请**：机器不能直接把某个地址加成成员。新成员仍收到真实邀请邮件、打开链接、在页面确认，
  由接受时的全部检查决定是否加入（`invites.accept`）。
* **不碰管理层**：机器能授予和操作的角色只有 `viewer`、`editor`。邀请、改成、改自或移除 `admin`/`owner`
  都返回 403 `role_needs_person`，这些仍由人在浏览器里完成（近期重新验证 + 理由）。所有权移交同理。
* **不借用个人身份**：服务账户不是某个人。邮件、站内通知、邀请落地页和审计都写明「集成 X（由 Y 批准）」，
  不冒充批准人。
* **不绕过频率与冻结**：工作空间每 24 小时 50 个邀请的上限、同一地址 60 秒冷却、冻结空间不增加权限，
  对机器同样适用（共用 `Invites`）。
* **用户委托暂不开放**：用户委托令牌仍不能调用目录接口。个人授权的集成代表个人管理成员，需要额外的同意页
  文案与风险评估，留作后续。

## 3. 身份与授权

| 项 | 设计 |
|---|---|
| 调用方 | 工作空间服务账户（API-5 同一资源 `urn:synwevo:platform:workspace`） |
| scope | 新增 `workspace.members.write`（与 `workspace.read` 并列，同一张审批里可以同时申请） |
| 审批 | 沿用 API 平台审批：平台管理员申请、空间所有者批准（近期重新验证）；只允许服务账户 |
| 权威链 | 服务账户的权限来自批准它的所有者。每次调用都复核：审批仍有效、批准人仍是该空间所有者（`Approvals.active`）、scope 仍获批 |
| 事务内复核 | 改角色、移除沿用 `Identity.change_role/remove_member` 的 `authorize` 回调：在写事务里重新读取目标当前角色，确认仍是 `viewer`/`editor`、新角色不超出上限、增加权限时空间未冻结 |
| 邀请归属 | `invitations.invited_by` = 批准人；`invited_via` = 服务账户 `client_id`；`invited_approval_id` = 原批准 ID。接受时复核原批准、当前客户端及写入 scope；撤回后重新批准不能复活旧邀请。失败为 409 `inviter_lost_permission` |
| 审计 | 沿用持久化枚举 `actor_kind='machine'`、`actor_id=client_id`，`meta.actor_kind='service_account'`、`meta.approved_by` 与 `approval_id` 记录授权来源；动作名与浏览器相同（`invite.create`、`invite.resend`、`invite.revoke`、`member.role`、`member.remove`）。API 控制台只收录这些动作的机器记录 |

## 4. 路由（`/api/workspace/v1`）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/invitations` | `{email, role: viewer\|editor, can_export?, expires_in_days?: 1–30}`，201；支持 `Idempotency-Key`（同键重放同一邀请，不再发信；改参 409） |
| POST | `/invitations/{iid}/resend` | 新链接、旧链接失效；只对本空间待接受或已过期的邀请；支持 `Idempotency-Key`，重发仍受 60 秒冷却 |
| POST | `/invitations/{iid}/revoke` | 撤回；已接受的返回 409 `invite_accepted` |
| PATCH | `/members/{user_id}` | `{role: viewer\|editor, expected_role: viewer\|editor}`：两字段必填；`expected_role` 与当前不符 409 `role_changed`，事务内比较与写入 |
| POST | `/members/{user_id}/remove` | `{reason, expected_role}`（原因至少 4 个字，写入审计）；只移除 `viewer`/`editor`；被移除的人收到站内通知，其待处理邀请一并撤销 |

幂等：邀请创建用平台的幂等存储（`synwevo_core.idempotency`，从 Docs 移入核心，Docs 路径保留兼容导入）；
SDK 只自动重试有幂等键的邀请创建/重发；改角色、撤回、移除不会自动重试。改角色成功后用旧 `expected_role`
重试会得到冲突，调用方应读取当前状态。移除后的重复请求得到 404 `no_member`。

权威契约由真实路由生成：`python -m synwevo_identity.cli workspace-openapi --out docs/integration/workspace-openapi.json`。
生成过程不打开数据库。管理台 `/api/admin/api-platform/openapi` 合并 Docs 与目录契约；账户服务独立运行时仍有目录契约。
Python 的 `WorkspaceClient` 与 TypeScript 的 `WorkspaceClient` 复用原 SDK 传输层，默认账户源站
`https://accounts.synwevo.com`，写入需显式申请 `workspace.members.write`。

## 升级与恢复边界

Store 只新增可空列；既有人类邀请归属与接受规则不变。网页不能把机器邀请提升为管理员/所有者，必须撤回后由本人重新邀请。
成员变更的安全日志沿用既有同步文件记录；后续数据库事务失败时，该撤权记录在恢复备份后仍可能保守生效。
这不是跨文件与数据库的原子提交；故障回归明确验证该行为，不能用删除安全记录来假装整体回滚。

退回不认识机器邀请的旧代码前，必须先在当前版本撤回集成发出的未接受邀请并停用目录写入服务身份，确认安全日志落盘。
旧版本不会复核新增批准字段，不能直接回滚并保留这些待接受链接。复制同理：带委托的未完成申请应在当前版本撤回，
或等待执行/失效后再降级；不得让旧执行器跳过 `requested_authority` 检查。所有处理在真实变更范围内执行，不能用生产数据演示。

## 5. 验收

* 批准含 `workspace.members.write` 的服务账户后：发邀请（邮件署名为集成与批准人）→ 新人接受 → 改为编辑 → 移除；
  每一步在审计里归属于服务账户。
* 仅 `workspace.read` 的服务账户写入 403；Docs 凭据、访问密钥 401/403；邀请或改成 `admin` 403 `role_needs_person`；
  操作管理员或所有者 403 `role_needs_person`。
* 所有者撤回审批后：服务账户写入立即失败；它发出的待接受邀请在接受时失效（`inviter_lost_permission`）。
* 同一 `Idempotency-Key` 重试只产生一个邀请和一封邮件；`expected_role` 不符 409；冻结空间不能邀请、不能升级。
