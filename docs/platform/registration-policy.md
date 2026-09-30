# 注册策略：D3 修复设计

状态：**实施待开始**。2026-09-30 的只读准备；`closed` 的下述推荐语义仍待用户偏好回复，不能视为已确认或已实现。用户随后要求完成当前B4轮次后停止goal，因此本设计仅留存，尚未实施；后续只有明确恢复工作时才重新确认语义并安排。

## 推荐语义与范围

将 `closed` 定义为禁止创建新的用户账户，包括关闭前已发出的有效邀请。已有账户的登录、身份关联和加入空间仍按原有账户状态、邀请与权限规则执行。

| 行为 | open | invite | closed（推荐） |
|---|---|---|---|
| 公开注册、外部身份首次建号 | 允许 | 拒绝 | 拒绝 |
| 有效邀请创建新账户 | 允许 | 允许 | 拒绝 |
| 已有 active 账户登录、关联身份、接受邀请 | 保持原规则 | 保持原规则 | 保持原规则 |
| 已落库 pending 账户继续邮箱验证与激活 | 保持安全激活流程 | 保持安全激活流程 | 保持安全激活流程 |

`closed` 控制新账户准入，不充当账户停用：不撤销会话、不解除成员关系，不把已开始的账户验证变成永久死路。关闭前已创建的 pending 账户可继续现有受限登录与安全激活；激活仍要求邮箱持有人设置最终口令，并撤销历史凭据。它不意味着 pending 账户提前获得正常业务访问。

关闭期间拒绝邀请建号时，不消费令牌、不创建账户、成员关系或会话；重新开放后，尚未失效的邀请仍可接受。邀请的创建、重发与撤销保持现有授权，不把暂停准入变成批量撤销邀请。Bootstrap、主机授权 CLI 和应用身份不是公开注册入口，不能被通用 `Identity.create_user()` 上的一刀切检查阻断。

覆盖表目前存在待统一的产品承诺：D3 将 closed 邀请建号列为缺陷，A21 旧验收仍允许它，末尾又将 closed 的含义列为待决策。用户偏好回复后，应统一这些承诺与三语言文案；本设计未改覆盖表。

## 已确认的执行入口

| 入口与当前实现 | 当前缺口 | 最小执行修复 |
|---|---|---|
| `POST /api/auth/register` → [registration.py](../../services/identity/synwevo_identity/registration.py) `build_router.register`，策略检查约 66 行，创建事务约 93 行 | 只允许 open，但检查在写锁外；关停与建号可交错 | 可保留早拒绝，取得 `Store.tx()` 后、创建前必须重新检查 open |
| `GET /auth/external/{pid}/callback` → [external.py](../../services/identity/synwevo_identity/external.py) `build_router.callback`，首次建号约 580 行 | 首次建号只允许 open，检查在写锁外 | 外部网络验证保持事务外；创建事务内重新读策略。已有绑定身份登录、已有账户的受控关联不受新账户准入限制 |
| `POST /api/auth/invitations/accept` → [invites.py](../../services/identity/synwevo_identity/invites.py) `Invites.accept/_accept`，约 254/317 行 | 整体已有写事务，但无账户分支不读策略 | 在该事务内重读当前账户和策略；仅新建账户分支拒绝 closed，失败保持邀请未消费 |
| `POST /api/auth/email/verify`（兼容 `/verify-email`）→ [auth_routes.py](../../services/identity/synwevo_identity/auth_routes.py) `build_recovery_router.verify_email`，约 854 行 | 不受注册策略限制 | 按推荐语义保持现有安全激活流程；新增回归证明关注册不扩大或撤销既有身份能力 |
| `GET/POST /api/admin/registration` → [admin_routes.py](../../services/identity/synwevo_identity/admin_routes.py)，约 116–145 行 | `expected_mode` 可省略，且 open→closed→open 后旧请求仍通过；响应在事务外重读 | 强制 revision，预览与提交绑定版本，事务内返回此次提交快照 |
| [identity.py](../../packages/core/synwevo_core/identity.py) `registration_mode`，约 854 行 | 有效 `meta['registration.mode']` 覆盖环境；否则 `SYNWEVO_REGISTRATION`，默认 invite | 保留兼容读入口，委托统一策略服务读取，不加缓存 |

## 有界实现方案

1. 在 core 增加 `RegistrationPolicy` 单键服务，统一返回 mode/source/revision。沿用唯一的 Store，使用现有 meta 增加单调 `registration.revision`；旧配置从 revision 0 起，不虚构过去的发布记录。mode 与 revision 在同一写事务中读取和变更。
2. 管理发布强制 `expected_revision`，不保留仅传 `expected_mode` 或省略版本的写旁路。复用 `SignedPreview` 与 `admin_operations`：预览绑定操作者、源站、动作、有效来源、前后值与 revision；取得写锁后重新检查当前权限、严格 admin 复验及版本。mode、revision、成功审计和幂等结果同事务提交；审计失败全部回滚。
3. 支持 `mode:null` 恢复继承：删除显式覆盖，同时递增 revision；预览明确将采用的环境值或 invite 默认值。显式覆盖与继承即使有效值相同也属于不同状态。已成功请求的相同幂等重试返回原结果，同键改参数拒绝。
4. 不把注册枚举塞进 B3 两项数值摘要预算的协议。本切片完成准入语义、并发发布与恢复继承；完整配置历史回滚、主机环境编辑和逐实例应用报告继续属于后续 G3 范围。
5. [GovernanceTabs.tsx](../../services/identity/web/src/governance/GovernanceTabs.tsx) 的 `RegistrationTab` 改为真实差异预览、版本冲突保留输入及重新比较；替换“其他两种策略只允许受邀者”。[Register.tsx](../../services/identity/web/src/pages/Register.tsx)、[Invite.tsx](../../services/identity/web/src/pages/Invite.tsx) 与邀请预览返回安全的准入提示，避免 closed 下仍承诺“创建账户并继续”。执行接口始终重新判断。closed 错误不能继续提示使用邀请即可建号。

## 兼容与部署边界

保留现有 GET 地址与 mode/source 字段，增加 revision。旧管理 POST 缺版本明确拒绝并要求刷新，不能为了旧客户端兼容绕过并发保护。已有数据库覆盖及环境回退顺序保持不变；恢复继承的含义是采用该进程的环境，不代表所有实例环境一致。签名预览须绑定读到的有效值和来源，提交时重新比较。

旧账户服务实例仍会允许 closed 邀请建号，也不会更新新 revision。必须更新所有账户服务写入进程后，才能宣称 D3 和并发保护生效；仅写入配置不是实例升级或应用确认。普通登录、已有会话和恢复渠道不得借部署被整体关停。

## 必须验收

- 三模式覆盖公开注册、受邀新账户、已有账户入组、外部新身份、已关联身份登录；关闭前旧邀请在 closed 下无任何建号副作用，恢复 invite 后可接受。
- pending 账户在关闭后仍沿用安全激活，active 账户密码/安全密钥/已关联外部身份登录不变；停用、删除等账户状态限制不被放宽。
- 两个 Store 在请求等待写锁时关闭注册，三个建号入口均重新判断；邀请仍未消费，无孤立账户、成员关系或会话。
- ABA、缺 revision、两管理员竞争、恢复继承、同键重试及参数冲突、审计失败回滚；旧环境覆盖与重启读取正确。
- 复用 `services/identity/tests/test_invites.py`、`test_registration_activation.py`、`test_external_identity.py`、`test_admin_governance.py` 及 `tests/integration/test_public_onboarding.py` 的隔离夹具；补真实浏览器文案、冲突与邀请阻断流程。所有数据均为临时夹具，不运行生产或真实外部邮件投递。
