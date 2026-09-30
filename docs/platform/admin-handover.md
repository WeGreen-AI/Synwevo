# 最高管理员主动接任与就绪证明（B4）

状态：**本地实现，收尾验收中，未推送或发布**（2026-09-30）。本文件记录 B4 已实现的接口与存储约束；最终证据另见本批质量记录。用户要求本轮完成后停止 goal，不继续后续批次。

## 1. 实际问题与交付边界

现有 `PlatformAccess.grant()` 立即授予最高角色。`is_usable_super_admin()` 仅检查有效授权、账户状态、邮箱验证及当前静态认证能力，不能证明接任人本人接受、实际进入后台或完成近期强认证。通用 `reauth_scope='admin'` 可以来自口令单因素；会话 `amr` 累计历史因素，不能证明本次操作刚完成 UV。

本批交付一条完整的正常接任链路：现有最高管理员发起提议 → 指定账户本人以专用 Passkey UV 主动接受 → 实际进入后台 → 以第二次专用 UV 确认就绪 → 按需独立撤销旧管理员授权。两次 UV 都验证真实签名、当前 RP/origin、UP 和 UV，不能以口令、历史 AMR、最近登录或时间戳推断替代。

复用唯一账户、会话、Store、Passkey 和角色模型；不新建平行账户或数据库。所有开发与验证使用临时夹具，不对真实身份授予权限。页面提供账户内接任入口，本批不发送邀请邮件或通知。

本批不强制现有唯一口令管理员立即迁移为全局 MFA，不把 TOTP 单独输入当成完整强认证。口令加 TOTP 的两个新鲜因素证明留待后续真实接线；本批接任证明只支持 Passkey UV。现有首位管理员初始化及受控主机恢复例外保持清楚、独立。

## 2. 现有能力与必须关闭的入口

| 现有位置 | 当前行为 | 本批处理 |
|---|---|---|
| `packages/core/synwevo_core/platform_access.py` | `grant()` 即授最高角色；统一有效权限、角色镜像、最后可用管理员检查 | 普通 `grant(SUPER_ADMIN)` 拒绝即时授予；专用接受流程共用内部授予逻辑；统一计算接任就绪 |
| `services/identity/synwevo_identity/admin_routes.py` | `/users/{uid}/platform-grants` 与兼容 `/platform-role` 均可立即授予最高角色 | 两条旧路径明确返回 `super_admin_acceptance_required`；其他角色与正常撤权保留 |
| `services/identity/synwevo_identity/passkeys.py` | 已有绑定用户、会话、scope、RP/origin、单次消费的真实 UV 重认证 | 复用签名验证，增加严格的专用上下文隔离 |
| `packages/core/synwevo_core/sessions.py` | `add_factor()` 记录实际因素；`confirm_reauth()` 同时更新时间及用途 | 专用 UV 只记录实际因素，不调用 `confirm_reauth()`，不扩展任何会话时限 |
| `services/identity/synwevo_identity/admin_operations.py` | 类型目录、签名预览、既有幂等结果表 | 复用，不创建通用审批框架 |
| `services/identity/synwevo_identity/profile.py`、`passkeys.py`、`admin_operations.py` | 删除账户、移除认证器、停用账户调用最后可用管理员判断 | 通过统一判断排除未就绪的新授权，补齐所有路径与竞态验证 |
| `packages/core/synwevo_core/journal.py` | 已有精确 grant 撤销日志及恢复后零可用管理员护栏 | 接受后撤销沿用现有日志，不新增伪兼容日志种类 |
| `apps/docs/scripts/snapshot.py` | 恢复使会话、登录及重认证挑战失效 | 额外使所有 pending 提议失效，普通恢复和 `--no-journal` 一致 |

服务层也必须关闭普通最高角色授予，不能只隐藏 UI 或仅在新 HTTP 路由强制接受。内部已证明的接受入口只读取服务器验证并绑定的提议，不接受公开请求传入 `accepted=true`、proof receipt 或任意授予来源。

## 3. 状态与可用性

| 阶段 | 是否持有最高角色 | 是否可进入并治理后台 | 是否计入最后可用管理员 |
|---|---|---|---|
| pending | 否 | 沿用原有权限 | 否 |
| accepted | 是，绑定唯一提议 | 是；危险动作仍需独立的普通 admin 重认证 | 否 |
| ready | 是 | 是 | 还须符合当前账户、有效授权和认证方式可用条件 |
| declined / cancelled / expired / invalidated | 未接受的提议不授予角色 | 沿用原有权限 | 否 |

仅 pending 提议可以取消或拒绝。接受后已有实际授权，不能以“取消提议”隐藏撤权；必须使用原有角色撤销服务、审计和 journal。已撤授权不能通过 ready 或重放接受响应重新生效。

提议期限固定为 24 小时，限制 pending 接受期限。接受后不自动到期或撤权；就绪阶段每次挑战仍短时有效。ready 是已经完成接任验证的持久事实，不随十分钟认证窗口到期，否则管理员数量会在无人操作时自动归零。会话的新鲜认证与当前认证器可用性继续独立检查。

普通发起者必须持有当前有效的最高授权；提议绑定其实际 `grant_id`。接受时该原始授权须仍有效，撤销后重新授予的新 ID 不能复活旧提议。接受提交后授权已经成立；就绪检查接任人自己的实际授权，不再要求原发起人的授权永久存在。

旧管理员不会自动退出。交接界面仅在 ready 后提供撤销旧授权的下一步。新接任人未 ready 时，不能令唯一旧管理员通过撤权、自删、停用或移除最后认证方式退出。若本来还有其他可用最高管理员，既有安全撤权仍按当前规则执行；不能让悬而未决的提议永久保护被盗用的旧管理员。

既有授权、迁移与 host/bootstrap 授权的 `offer_id` 为 NULL，保留当前资格规则，避免迁移时把唯一管理员变为不可用。此例外必须显示为既有/主机来源，不假称已完成本批接任证明。

## 4. 最小数据增量

所有表与迁移均在唯一 `Store` 中。

新增 `platform_super_admin_offers`：

- 不可变提议：`id`、`proposer_id`、`proposer_grant_id`、`recipient_id`、`reason`、`created_at`、`expires_at`。
- 状态：`state`；终止说明 `closed_at`、`closed_by`、`closed_reason`。恢复失效与用户取消使用不同固定原因。
- 接受：唯一 `accepted_grant_id`、`accepted_at`、`accepted_session_id`、`accepted_challenge_id`、`accepted_passkey_id`。
- 就绪：`ready_at`、`ready_session_id`、`ready_challenge_id`、`ready_passkey_id`。

`platform_role_grants` 增加可空、唯一的 `offer_id`。`is_usable_super_admin()` 对非空来源必须核对同一提议、同一接受授权和 ready 状态；不存在或不匹配的关联不得算可用。现有有效权限读取仍允许 accepted 的真实角色进入后台。

`reauth_challenges` 增加可空 `context_kind`、`context_id`。通用重认证两者均为 NULL；专用上下文分别为 `super_admin_accept` 和 `super_admin_ready`，`context_id` 是服务器读取的不可变提议 ID。就绪挑战再通过该提议的不可变 `accepted_grant_id` 绑定实际角色。上下文不是客户端可以自由传入通用 reauth 的字段。

不保存口令、assertion 正文、私钥或额外 bearer token。证明引用仅为内部稳定 ID。HTTP 列表不返回会话/挑战引用。沿用 `admin_operations` 保存 actor + idempotency key、规范化请求摘要和结果，不另建操作数据库。

每个接任人同时最多一条未过期 pending 提议；创建前持锁关闭已过期提议。每个发起者未过期 pending 提议设明确上限，结合现有限流。列表默认 20 条、最大 50 条，用稳定游标分页；返回当前及最近终态，明确显示过期、授权变化、恢复失效原因，不将所有过期记录悄悄隐藏。

## 5. HTTP 契约

以下已实现接口均使用账户源中央会话，遵守已有 CSRF/origin 检查；GET 不写状态。列表和返回对象只含授权范围内的提议。

通用 `offer` 形状：

```json
{
  "id": "offer-id",
  "status": "pending",
  "proposer": {"id": "existing-user-id", "display_name": "发起者", "email": "fixture@example.test"},
  "recipient": {"id": "existing-user-id", "display_name": "接任者", "email": "fixture2@example.test"},
  "reason": "由发起者填写的授权理由",
  "created_at": 0,
  "expires_at": 0,
  "accepted_at": null,
  "ready_at": null,
  "grant_id": null,
  "can_accept": false,
  "required_method": "passkey_uv",
  "unavailable_reason": "passkey_required"
}
```

上例为契约占位夹具，不是真实身份或有效时间。`status` 包含持久状态及根据当前权限/授权派生的不可用状态。`can_accept` 是界面提示，执行时必须重新检查。安全设置入口是现有 `/account#security`，本人接任区为 `/account#admin-handover`，后台就绪区为 `/admin#admin-handover-ready`。

| 请求 | 输入 | 主要响应 / 权限 |
|---|---|---|
| `GET /api/admin/super-admin-offers?limit=20&before=…` | 分页 | `{items,next_cursor}`；当前可授予最高角色的管理员 |
| `POST /api/admin/super-admin-offers/preview` | `{target_user_id}` | 现有高风险签名预览；绑定 actor、原授予 ID、目标、源站、期限及前置状态 |
| `POST /api/admin/super-admin-offers` | `{target_user_id,preview_token,idempotency_key,reason}` | `{operation_id,offer}`；当前最高授权及独立严格 admin 重认证；理由 8–500 字 |
| `POST /api/admin/super-admin-offers/{id}/cancel` | `{idempotency_key,reason}` | `{operation_id,offer}`；当前可授予最高角色的管理员，限定 pending |
| `GET /api/auth/super-admin-offers?limit=20&before=…` | 分页 | `{items,next_cursor}`；仅本人接收的提议，无需平台权限 |
| `POST /api/auth/super-admin-offers/{id}/decline` | `{idempotency_key}` | `{operation_id,offer}`；本人明确拒绝 pending |
| `POST /api/auth/super-admin-offers/{id}/accept/begin` | 空对象 | `{options,offer}`；本人当前会话、有效 pending 提议、当前 RP 下可用 UV key |
| `POST /api/auth/super-admin-offers/{id}/accept/finish` | `{credential,idempotency_key}` | `{operation_id,offer,grant_id,next:'admin_ready'}`；真实专用 UV 成功后原子授予 |
| `GET /api/admin/super-admin-offers/{id}/readiness` | 无 | `{offer,can_confirm,methods:['passkey_uv']}`；本人持有该提议的实际最高授权，并通过当前后台权限检查 |
| `POST /api/admin/super-admin-offers/{id}/ready/begin` | 空对象 | `{options,offer}`；同一授权及当前后台权限，独立 ready 挑战 |
| `POST /api/admin/super-admin-offers/{id}/ready/finish` | `{credential,idempotency_key}` | `{operation_id,offer,usable_super_admin:true}`；第二次专用 UV 成功，事务内重验当前可用性 |

pending 目标只要求是现有 active、邮箱已验证的人类账户，可以暂时没有 UV；创建提议不授予任何新权限。界面说明本人需在现有账户安全页设置 Passkey。accept/begin 才强制当前 RP 的可用 UV key，finish 再持锁复核。不会要求发起人线下猜测对方是否准备好。

接任者接受后刷新现有 `/api/auth/me`/账户权限上下文，进入现有后台的就绪区。完成 ready 后撤销旧授权仍调用已有角色撤销接口和独立 ActionDialog。专用接受/就绪证明不作为普通 admin scope 通行证。

## 6. 专用证明、事务与并发

1. 通用 reauth begin 只能创建 NULL context。通用 spend **和** verify 显式拒绝任何非 NULL context；专用端点反向拒绝通用、其他提议及另一阶段的挑战。旧登录使用不同挑战表，保持隔离。
2. 每次专用挑战由服务端绑定当前 user/session、scope、purpose、RP/origin、提议和阶段，有短时 expiry 与条件单次消费。不能靠“挑战创建时间晚于提议”、累计 AMR 或客户端声明证明关联。
3. 沿用已实现的先独立事务消费挑战，再写事务验证签名模式。后续失败不能令捕获的 assertion 重新可用；失败后只能发起新挑战。
4. 成功事务持 writer 锁重新校验会话撤销/idle/absolute、账户状态、当前 Passkey、提议状态及准确 grant ID。密码/OTP/登录历史不满足专用 UV。grantor 撤销再重授、目标注销或删 key 与接受并发均按当前存储事实拒绝。
5. 接受的证明引用、实际授权、回滚镜像、会话策略收紧、`passkey_uv` 实际因素、操作结果及成功审计同事务提交。ready 的证明引用、状态、实际因素、操作结果及审计同事务提交。审计失败回滚全部成功状态；独立消费的挑战保持已用。
6. 专用成功只在已复核的当前会话调用 `Sessions.add_factor(..., 'passkey_uv')`，不因证明而延长 `reauth_at`、`reauth_scope`、idle 或 absolute；其中用途及确认时间保持原值，角色授予可按现有管理员策略缩短 idle。既有 mfa scope 或没有 admin scope 的会话在接受/就绪后仍不能执行普通严格 admin 操作。
7. 幂等键绑定 actor、动作、提议、源站和规范化请求。相同 key 不同参数报冲突。成功重试先在持锁状态检查现有结果，避免要求再次消费已用挑战；仍须当前会话、本人身份和未撤销的相关授权。并发接受最多一个实际 grant；旧结果不得复活后来已撤授权。
8. 取消/拒绝与接受互斥；accepted 后普通取消明确拒绝。失效、异常及失败审计只记录固定 code/稳定 ID，不记 credential 或异常正文。begin/finish 复用现有节流并有界清理过期挑战。

接受审计区分实际操作者（接任人）与授权来源（发起者、原授予 ID），不能把发起者冒充为执行接受的人。审计至少覆盖提议创建、取消/拒绝、接受、就绪及失败；角色撤销沿用既有记录。

## 7. 恢复与代码降级

- 任意恢复一律使 pending 提议及所有挑战失效，包括 `--no-journal`。因此恢复旧备份不能重新开放后来已取消/拒绝的 pending 授权意图。列表解释 `restored_invalidated`，不自动发新提议。
- accepted 已产生实际授权；其撤销使用现有精确 grant ID journal。恢复保留 accepted/ready 的历史状态，但撤销全部旧会话和挑战；未 ready 的接任人需新会话、新专用 UV 才能就绪。
- 备份若只有旧 root 与 accepted-but-not-ready 的接任者，而后来撤掉旧 root，journal 重放必须因零可用管理员拒绝恢复。不能复活已撤旧身份；需包含合法 ready 接任者的更新备份或另行受控恢复。
- 原本未初始化、开始就零可用管理员的备份仍可恢复/初始化，保持已有 guard 的初始零例外。新增规则不得把既有唯一授权错误判为零从而绕过该 guard。
- `bootstrap admin --force` 是现有主机权限工具，仍能直接创建新管理员；不是本批正常交接协议，也不是已完成的限时恢复协议。不从普通 HTTP 暴露此来源，不为测试实际运行到生产库。
- `--no-journal` 无法重建人类账户和平台授权撤销历史；会话重新登录不能证明授权已经安全核对。本批只关闭 pending 提议恢复，**不宣称解决无日志恢复授权复活**，也不悄悄改变原有受控逃生语义。
- schema 为增量不等于治理语义可以安全降级。旧代码不识别专用 context、ready 计数及即时授予禁令；直接降级会丢失这些约束。降级前必须停止新提议并完成/撤销未就绪授权，保留理解现有 journal 的恢复工具，并明确旧版本不提供本批接任保障。不得删日志或改镜像以冒充兼容。

## 8. 实施归属

| 负责人 | 文件 / 范围 |
|---|---|
| 后端代理 | `packages/core/synwevo_core/store.py` 唯一 schema 迁移；`platform_access.py` 最高授予入口、可用性与内部受控授予 |
| 后端代理 | `services/identity/synwevo_identity/admin_handover.py` 新操作/路由；`passkeys.py` 严格上下文绑定；必要时 `sessions.py` 小范围因素记录辅助，不扩会话语义 |
| 后端代理 | `app.py` 集成；`admin_operations.py` 类型目录登记；`admin_routes.py` 旧最高角色路径关闭 |
| 后端代理 | `apps/docs/scripts/snapshot.py` pending 恢复失效；必要的 `profile.py`/撤权状态同步；不扩展无日志恢复协议 |
| 后端代理 | `services/identity/tests/test_admin_handover.py`、权限矩阵及相关原有测试适配；`tests/integration/` 恢复窄测；不降低已有安全断言 |
| 根代理 | `PlatformAccess.tsx` 发起/状态/撤权引导、`Account.tsx` 本人接任区、后台就绪区；复用现有 Passkey 浏览器调用及操作 UI，维护三语言 |
| 根代理 / 验证代理 | 真实浏览器临时夹具、移动/键盘验证；覆盖表、使用/恢复说明、最终门禁与交付记录 |

共享源码修改开始前由根代理确认已有验证回归完成和文件占用。此设计文件不替代实施验收。

## 9. 验收清单

- 普通注册及无关用户无平台权限；pending 无新增角色；目标尚无 UV 时可收到明确设置指引，无法伪造接受。
- 真实软件签名与 Chromium 虚拟 UV 完成两次独立 ceremony、实际后台进入和旧授权撤销；没有邮件外发。
- 错用户/session/origin/RP/offer/stage、过期/重放/无 UV/无 UP/错误 userHandle/已删 key 全部拒绝。普通 reauth 与专用证明双向不可消费。
- 口令重认证、旧 `passkey_uv` AMR、提前完成的通用 UV 不满足接受或 ready；两次专用成功不刷新普通 admin scope。
- grantor 原授权撤销后重授不能接受旧提议；接受后本人授权撤销再授也不能 ready 旧提议。注销、账户停用、删 key 与 finish 的 writer 竞态按当前事实检查。
- pending/accepted 未 ready 时，唯一旧 root 的撤权、自删、停用及最后因子移除均拒绝；现有其他可用管理员的合法安全处置不被提议永久阻挡。完成 ready 不因时间流逝自动失效。
- 两 Store 并发接受、取消/接受、就绪/撤权以及双方撤权不产生重复授权或零可用管理员。相同幂等请求不重做；改参数冲突；审计/结果写入失败不留下半成品。
- 普通最高 grant 与 legacy platform-role 不能绕过；非最高角色保持行为；原有合法初始化、`--force` 主机例外测试保留。
- 带 journal 与无 journal 恢复均作废 pending 和 challenge；已接受授权撤销按精确 ID 重放；缺少 ready 接任者的备份拒绝归零，初始零管理员库仍可恢复初始化。
- 三语言、窄屏、键盘、失效状态、限量分页及返回账户/后台导航验证通过；日志和产物不含 credential、会话秘密或真实用户数据。

新增稳定错误包括：`super_admin_acceptance_required`、`super_offer_not_found`、`super_offer_expired`、`super_offer_state_conflict`、`super_offer_authority_changed`、`super_offer_passkey_required`、`super_offer_unready`、`super_offer_limit`。沿用已有 `no_challenge`、`bad_assertion`、`idempotency_conflict` 和认证/权限错误。固定拒绝句子已接入简体中文、繁体中文和英文，并由拒绝契约测试检查账户与管理员两个页面的实际模块图。

## 10. 后端隔离验证记录（2026-09-30）

- 专用接任、既有 Passkey 重认证、管理权限矩阵、权限竞态、平台/账户撤权恢复、真实 snapshot journal 和 blob 引用分类共 **184 passed**，7 warnings，72.38 秒。日志：`/private/tmp/b4-backend-final.log`。
- 独立审查补充的“接任已接受后，原发起者被其他最高管理员合法撤权，接任者仍可就绪”用例 **1 passed**。日志：`/private/tmp/b4-accepted-independence.log`。上述共 185 个不同窄组用例，接任专用文件现有 37 个。
- 架构边界检查通过，覆盖 524 个 Python 文件、256 个前端文件；日志：`/private/tmp/b4-boundaries.log`。
- 完整 identity + integration 套件运行中，日志：`/private/tmp/b4-identity-integration-full.log`。其结果与真实浏览器结果尚未包含在上述通过数内。
- 全部身份、密钥、数据库和恢复副本都是临时测试夹具。普通即时最高授予入口已关闭；旧镜像/恢复/会话测试明确使用 host fixture 验证原有不变量，不作为主动接任成功的证据。
