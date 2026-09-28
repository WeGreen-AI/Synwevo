# 组织自定义角色（组织级权限的命名集合）

> 状态：2026-09-26 实现（CI-23）。本文写的是**当前代码的行为**；未做的部分在 §7。

## 0. 出发点：现状有什么、没有什么（实现前）

| 已有 | 位置 | 缺口 |
|---|---|---|
| 组织基础角色 owner / admin / member | `organization_memberships.role` | 只有三档；"能邀请但不能改设置"这种委派做不到 |
| 工作空间角色 viewer / editor / admin / owner + `can_export` | `memberships` | 与组织角色分离（保留） |
| 组织操作的权限检查 | `Organizations.require(manage=/owner=)` | 以角色名硬编码，无法按能力委派 |
| 邀请策略 admins / owners | `organizations.invitation_policy` | 只认基础角色 |

没有：权限目录、自定义角色表、"该成员能做什么、来自哪里"的视图。

## 1. 决策记录

| 项目 | 结论 |
|---|---|
| 问题与基线 | 组织拥有者要么把人提成管理员（得到全部管理能力），要么什么也给不了；成员导入、审批、群管理等新能力会让这个问题更重。 |
| 成熟参考 | Slack 自定义角色（读取 2026-09-26）：组织级、按权限勾选、可编辑/删除、只有 Primary Owner 与 Roles Admin 可创建；Notion 企业管理（读取 2026-09-26）：「Admin roles don't change what someone can see or edit in pages or databases」——管理权不等于内容权；Membership Admin 只能加成员、不能加管理员——委派不得越过委派者。 |
| 对比与选择 | (a) 把工作空间角色扩成权限位：会改变 Docs 的对象授权契约，风险大；(b) 只加一个「roles admin」标志：仍是固定档；(c) 组织级权限目录 + 自定义角色表：不碰工作空间与文档授权，现有三档角色保留其含义并映射到目录。选 (c)。 |
| 预期变化 | 组织页新增「自定义角色」节（创建/编辑/删除，权限按组勾选，自己没有的权限不可勾）；成员行显示自定义角色，可分配、可查看有效权限及来源；组织级检查（设置、成员、空间、邀请）改按权限判定；接口 `/api/auth/organizations/{oid}/roles*`、`/members/{uid}/roles`、`/members/{uid}/permissions`、`/permissions`。 |
| 边界与风险 | 委派不越权：创建/修改/分配角色都要求操作者已持有其中每一项权限并持有 `org.roles.manage`；所有者专属权限（交接、生命周期）不可放进角色；删除已分配的角色必须明确「同时解除分配」；成员被移除时角色一并清除；工作空间与 Docs 权限完全不受影响；目录新增权限不会自动进入已有角色。 |
| 验收 | §6：成员被分配「建空间」角色后能建空间、仍不能改设置/管角色；受委派的角色管理员不能铸造自己没有的权限、不能编辑/分配含更高权限的角色；所有者也不能把所有者专属权限放进角色；「仅所有者」邀请策略压过任何自定义角色；删除已分配角色 409 → 明确 unassign；移除成员清角色；admin 基础角色行为不变（仍不管角色）。 |
| 发布与恢复 | schema v9→v10 新增两张表；现有成员与角色不迁移（三档角色映射到目录即刻生效）；回滚只需回退代码，表保留无害。 |

## 2. 权限目录（稳定 ID，三语言标签在 `org_roles.CATALOGUE`）

| ID | 组 | 含义 | admin 默认 | 可委派 |
|---|---|---|---|---|
| `org.settings.manage` | organization | 改名、头像 | ✓ | ✓ |
| `org.members.view` | members | 成员列表 | ✓ | ✓ |
| `org.members.invite` | members | 向组织空间发邀请（受邀请策略约束） | ✓ | ✓ |
| `org.members.manage` | members | 改普通成员的组织角色、移除普通成员（管理员/所有者仍只由所有者处理） | ✓ | ✓ |
| `org.members.import` | members | 批量导入（CI-25） | ✓ | ✓ |
| `org.roles.manage` | roles | 管理与分配自定义角色 | — | ✓（所有者委派） |
| `org.workspaces.create` / `org.workspaces.adopt` | workspaces | 建空间 / 纳入空间 | ✓ | ✓ |
| `org.audit.read` | governance | 组织审计 | ✓ | ✓ |
| `org.approvals.decide` | governance | 复制审批（CI-27，还需目标空间管理员；见 project-copy.md） | ✓ | ✓ |
| `org.messaging.manage` | governance | 群聊治理（CI-26） | ✓ | ✓ |
| `org.ownership.transfer` / `org.lifecycle.manage` | owner | 交接所有权 / 冻结删除恢复 | — | ✗ 仅所有者 |

有效权限 = 基础角色集合 ∪ 已分配角色的并集。普通成员默认为空集（与实现前一致：成员看不到成员列表）。

## 3. 数据与接口

* `organization_roles(id, organization_id, name UNIQUE per org, description, permissions JSON, version …)`、
  `organization_role_assignments(organization_id, user_id, role_id)`；每组织最多 50 个角色。
* `GET /{oid}/permissions`：读者语言的目录 + 调用者持有的权限；`GET /{oid}/roles`（需 `org.members.view`）；
  `POST/PATCH/DELETE /{oid}/roles[/{rid}]`（敏感操作：重认证 + 理由）；`PUT /{oid}/members/{uid}/roles {role_ids}`（整体替换）；
  `GET /{oid}/members/{uid}/permissions` → `{base_role, roles, permissions, sources}`。
* 审计：`organization.role.create/update/delete/assign`；分配变化时当事成员收到通知 `organization.roles_assigned`。

## 4. 权限判定的接入点

`Organizations.require(oid, actor, permission=…)`：设置与头像 `org.settings.manage`；建空间/纳入空间 `org.workspaces.create/adopt`；
成员列表 `org.members.view`；改角色/移除 `org.members.manage`（原有"只有所有者能处理管理员与所有者"的规则保留）；
邀请：策略为 owners 时只认基础 owner，否则看 `org.members.invite`。所有者专属操作继续用 `owner=True`。

## 5. 界面

组织详情页：`OrganizationRoles` 节（列表、创建/编辑对话框按组勾选、删除时说明受影响人数并解除分配）；成员节每行显示自定义角色徽标，
「分配角色」对话框勾选角色，「查看权限」显示有效权限与来源（`base:admin` / `role:<id>`）；所有对话框走既有的理由 + 重认证流程。
三语言消息在 `organization.messages.ts`。

## 6. 验收证据

`services/identity/tests/test_org_roles.py`（5 项）：目录与基础集合一致；所有者建角色分配后成员恰好获得那些权限（能建空间、能看成员，不能改设置/管角色）并收到通知；
受委派的角色管理员不能铸造未持有的权限、不能编辑或分配更高的角色、能分配自己创建的角色，邀请权限经 `require_invitation_permission` 生效，
「仅所有者」策略压过角色；删除已分配角色 409 → `unassign` 后清除，同名/未知权限/版本冲突分别拒绝，移除成员清角色；admin 基础角色行为不变。
浏览器实跑见 `docs/quality/verification.md` §16.23。

## 7. 明确未做

* `org.members.import`、`org.approvals.decide`、`org.messaging.manage` 由各自功能消费；组织审计见 §8。
* 工作空间级自定义角色：不做——工作空间与文档授权保持四档角色。
* ~~复制角色：用「编辑」即可~~ CI-94（2026-09-28）已做：`POST /{oid}/roles/{rid}/copy {name?}`（敏感操作，与创建同一流程）。复制即创建：操作者须持有源角色的每一项权限（持有才可授予），计入 50 个上限与名称唯一，副本不分配给任何人；不给名称时取「源名称 (2)」「(3)」…（与语言无关的后缀）。审计 `organization.role.create` 带 `copied_from`。界面：角色行的「复制」按钮，含未持有权限或已达上限时禁用并说明原因，对话框预填「名称（副本）」。角色模板仍未做。

## 8. 组织审计（CI-101）

`org.audit.read` 此前在目录里却没有任何东西可读——一个能授予、却什么都不给的权限。现在它读的是组织的治理日志：
`GET /api/auth/organizations/{oid}/audit?before=&limit=&category=`（`synwevo_core.organization_audit`），组织页的「组织审计」一节。

| 项 | 规则 |
|---|---|
| 谁能读 | 持有 `org.audit.read`：所有者、管理员（基础角色含此权限），或通过自定义角色获得的成员；组织冻结时仍可读 |
| 有什么 | 组织自身的事件（创建、设置、所有权、冻结/删除/恢复、头像、工作空间创建与纳入、成员角色与移除/退出、自定义角色、成员导入、复制审批），以及组织各工作空间的**成员与邀请**事件（邀请、重发、撤回、接受、拒绝，角色变更，移除、退出，应用使用资格，所有权交接）——**只从工作空间属于组织时算起**：纳入的旧工作空间不带来此前的记录（此前的移除、原因与人名） |
| 没有什么 | Synwevo Docs 与 Synwevo Message 的业务事件、任何人的登录与安全活动、IP 地址、请求 id、被邀请人的邮箱、自由格式的元数据 |
| 每条显示 | 时间、谁（成员的显示名；平台管理员与系统按身份标明；不在组织中的人只显示其自设的显示名，从不取其邮箱）、做了什么（三语言动作名）、对象（成员、角色、工作空间的**当前**名字）、结果，以及白名单细节：填写的原因、角色前后、增减的自定义角色、权限数量、导入的邀请/更新数、已核对的识别行数、审批的批准/拒绝 |
| 分页与筛选 | 按序号倒序，`before` 翻页，每页至多 100；类别：组织本身、成员变动、邀请记录、角色、批量导入、审批 |
| 性能 | 三部分各走索引再合并：组织自身事件 `(object_kind, object_id, seq)`、`organization.*` 事件按动作前缀 `(action, seq)`（两者为新索引，Store 打开时建立，幂等；100 万行合成表上建索引约 0.4 s）、各工作空间事件已有的 `(workspace_id, at)`；每部分只取一页 |
| 归档正确 | 工作空间事件按其自身的 `workspace_id` 归档：撤回邀请现在写入邀请所属的工作空间（此前取调用者当前空间，可被请求头带到别的组织）；组织事件里的 `workspace_id` 列只是操作者当时所在的空间，显示时改用事件元数据里的工作空间 |

验收：`services/identity/tests/test_org_audit.py`（4 项：内容与名字、谁能读、分页与类别、审查发现——撤回归档、纳入前的历史、组织外的人不取邮箱、超大游标 422）；隔离实例浏览器证明（桌面、英文、手机宽度）。
