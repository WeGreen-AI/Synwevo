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

* 组织审计页（`org.audit.read` 已在目录，页面随后续批次）；`org.members.import`、`org.approvals.decide`、`org.messaging.manage` 由各自功能消费。
* 工作空间级自定义角色：不做——工作空间与文档授权保持四档角色。
* 角色模板/复制角色：用「编辑」即可，暂不加入口。
