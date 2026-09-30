# Synwevo Admin 管理能力覆盖表

> 本表于2026-09-29建立，当前进度见下节。历史盘点依据是 7 个分区对 HEAD `e54b66e` 的只读代码核查：没有启动服务，没有读 `data/`、`.env`、秘密或用户内容。当时记录生产运行 `aadcb9e`，本轮未重新核实生产版本。
> 本文是管理员平台唯一的覆盖表。专项约定见 [`../ai/admin-platform.md`](../ai/admin-platform.md)，设计调研见 [`admin-references.md`](admin-references.md)。行号取自 `e54b66e`，修改前先复核。旧记录里的"完成"只当线索。每完成一条链路，就在这里更新状态、证据和发布状态。

## 2026-09-30 接续核查（当前批次，覆盖下面历史快照）

- B1-2 实现确实已存在于未合入的 `worktree-wf_a24f2626-a32-1`，本轮在 `codex/admin-platform` 继承并审查；历史“无权限目录”描述只适用于主检出旧快照。
- B1-1 已接入共用管理外壳，导航由真实 `/api/admin/session` 权限驱动，保留旧 `?tab=` 与 API 子路由；没有创建不存在的模块页面。
- B1-3/4 本地实现并通过专项验收；冻结提交dde47cc的容器全量在3GB限制下OOM，门禁未通过：`ac/admin_operations.py` 的 `admin.user.status`，权限 `platform.users.manage`，平台范围/单账户/高风险；只读影响预览签名绑定操作者、目标、参数、源站、资源版本和5分钟时限；实际执行重新检查当前会话/权限；幂等结果持久到唯一 Store 的 `admin_operations` 表。
- 停用允许 active/pending 普通人类账户，持平台角色者先撤权；恢复只允许 suspended，邮箱未验证保持 pending。状态、全部会话/委托令牌、未完成认证票据和成功审计同事务；恢复不复活旧凭据。
- 会话撤销、授予/撤销与兼容角色路由、注册配置、邮件及空间冻结写入增加事务内会话/权限复核。拒绝记录在失败事务外保留。
- B2已实现初始化事务与短时令牌期限校验、口令/Passkey UV按用途重认证、最后可用最高管理员的验证方式保护；真实Chromium虚拟认证器11条链路通过，三语320/390触摸布局与SPA离开取消已证。主机关联回归505项通过，未部署；详见 [B2记录](../quality/admin-b2-20260930.md)。
- 配置执行依赖：通知邮件派发已补写锁内预算/当前资格复核与认领-outbox-关联原子性，72项关联回归通过；保持私信邮件默认关闭。动态配置版本与管理界面仍在实施，见 [派发证据](../quality/admin-config-dispatch-20260930.md)。
- 此批仍未部署；Actions `36569205283` 的真实阻塞注解已于2026-09-30重新读取。全局搜索、保存视图、完整配置版本、所有业务治理和主机运维尚未交付，下面未实现项继续有效。
- 证据见 [本批验证记录](../quality/admin-b1-20260930.md)：前端252项、部署工具8项、浏览器10链路、Docs跨模块2项；主机全量首次2998通过/7失败/2跳过，失败均已窄修通过，冻结源码容器在约83%被OOM终止，待定位与重验。专项测试组互有重叠，不累加，不沿用历史通过数。

使用与恢复说明：[admin-operations.md](admin-operations.md)。本批截图与浏览器报告在 `output/playwright/admin-review/`（明确隔离fixture，无生产数据）。

## 0. 读法

**状态**（对应 §1.2 的四类：已接入 / 需补齐 / 缺外部配置 / 不适用）

| 值 | 含义 |
|---|---|
| 已接入 | 平台后台有入口，服务端真实执行 |
| 已接入（租户） | 功能真实可用，但只在组织或空间的自助界面里，平台后台没有 |
| 已接入·有缺陷 | 已接入，但存在 §1.4 列出的缺陷 |
| 缺界面 | 服务层已有，缺管理接口或界面 |
| 缺后端 | 服务或执行点不存在，含写死在代码里的常量 |
| 仅命令行 / 仅主机 | 只能通过 CLI、主机环境变量、compose 或 GitHub 操作 |
| 缺外部配置 | 依赖仓库外的凭据或服务 |
| 不适用 | 不建，原因写在"缺失"列 |

**所需权限简写**

| 简写 | 含义 |
|---|---|
| PA | 账户有效，`users.platform_role='admin'`，且会话 `admin_capable`（口令、`passkey_uv` 或两个不同因素）。无角色返回 404，未 step-up 返回 403 `step_up_required` |
| PA! | PA 加严格重认证（scope `admin`，`REAUTH_S` 默认 600 s），再加 8–500 字理由 |
| APM / APM! | `api_platform.manage`。今天由 PA 派生；带 `!` 的同样要求重认证与理由 |
| 租户 | 组织权限 `org.*`，或空间 owner/admin |
| 主机 | 主机 root 或容器 shell，应用内没有权限检查 |
| 代码 | 代码常量，改动需要发版 |
| 目标 `…` | 批次 1 权限目录草案里的 ID（见 §3.1） |

**路径简写**

| 简写 | 实际路径 |
|---|---|
| `core/` | `packages/core/synwevo_core/` |
| `ac/` | `services/identity/synwevo_identity/` |
| `acweb/` | `services/identity/web/src/` |
| `authc/` | `packages/auth-client/python/synwevo_auth_client/` |
| `dw/` | `apps/docs/synwevo_docs/` |
| `dws/` | `apps/docs/scripts/` |
| `msg/` | `apps/messages/synwevo_messages/` |
| `portal/` | `apps/portal/synwevo_portal/` |
| `portalweb/` | `apps/portal/web/src/` |
| `srv/` | `infra/server/synwevo_server/` |
| `prod/` | `infra/production/` |
| `ac-tests/` | `services/identity/tests/` |
| `it/` | `tests/integration/` |

**发布状态**：按 `git diff aadcb9e..e54b66e` 核对。

| 值 | 含义 |
|---|---|
| 已上线 | 依赖的代码在 `aadcb9e` 中，此后没有改动。只说明代码在线上，线上行为本轮没有复查 |
| 已上线·本地有改动 | `aadcb9e` 已有；之后有已提交、未部署的改动（HEAD 比生产多 117 个提交） |
| 仅本地 | `aadcb9e` 之后才新增，未部署 |
| 值待核实 | 代码已上线；生产环境变量的取值本轮没有读 |
| 未实现 / — | 不存在，或不适用 |

## 1. 现状

### 1.1 入口与权限模型

- **入口**
  - 管理后台在账户源 `accounts.synwevo.com/admin`（`acweb/main.tsx:125-126`），是一个单页，用 `?tab=` 切换 8 个标签：概览、账户、工作空间、组织、注册策略、登录方式、邮件投递、审计（`acweb/Admin.tsx:84-104`）。
  - 另有 `/admin/api-platform`，即 API 管理控制台，有独立页面和自己的标签栏。
  - 门户和账户顶栏只给平台管理员显示"平台管理""API 管理"链接（`portalweb/Portal.tsx:140-162`、`acweb/components/Topbar.tsx:57-68`）。
  - 按 §1.1"优先升级现有管理员入口"，批次 1 升级这个入口，不另建 `workplace/admin`，也不开新子域。
- **角色存储**
  - 只有一列：`users.platform_role ∈ {none, operator, admin}`（`core/store.py:128-131`）。
  - 没有角色表、权限表或授予表。谁在什么时候被授予，只能从审计的 `admin.user.platform_role`、`auth.bootstrap`、`cli.bootstrap.admin` 里查。
  - `operator` 没有任何能力，唯一的作用是触发 `demote_first`。
- **服务端判定 `_admin`**（`ac/admin_routes.py:37-64`），按顺序：
  1. 必须有会话。
  2. 账户必须是 active，否则 401。
  3. 必须是 `platform_role=admin`，否则 404，与不存在的页面同形。
  4. 必须 `admin_capable(amr)`，否则 403 `step_up_required`。
  5. 危险动作还要严格重认证，否则 403 `reauth_required`。
  6. 写操作要写 8–500 字理由。
  - 另外：HTML 外壳对非管理员同样返回 404（`core/http.py:833-839`）；`/api` 和 `/admin` 的响应都是 no-store；状态变更用双提交 CSRF。
- **`api_platform.manage`**
  - 不存储。`describe_user` 按"有效管理员且会话可用"派生（`authc/session_routes.py:112-119`），`require_manage` 再检查一次角色（`core/api_platform.py:59-65`）。
  - API 控制台的 step-up 和重认证规则与 `_admin` 相同，而且它会审计被拒绝的请求（`ac/api_platform.py:68-80`）。
- **管理员会话**
  - 闲置 1 小时即失效，不记住设备。这个规则只在创建会话时生效（`ac/auth_routes.py:657-660`）。
  - 管理员用 Passkey 登录时必须做用户验证（`auth_routes.py:346-352`）。
- **初始化与应急**
  - CLI 生成一次性令牌（900 s，只能用一次，新令牌会作废旧令牌），配合 `POST /api/auth/bootstrap` 使用；已经存在有效管理员时返回 409。
  - `bootstrap admin --force` 直接新建一个管理员。
  - 没有 CLI 能把已有账户提升为管理员。
- **最后管理员保护**
  - 降级时在事务内计数（`core/identity.py:787-791`）。
  - admin 或 operator 必须先降级才能被停用（`demote_first`）。
  - 最后一名管理员不能自删（`ac/profile.py:74-76`）。
  - 缺陷：计数包含应用身份，也包含无法完成重认证的管理员。
- **内容边界**：平台管理员不获得任何工作空间文档的读取权（`docs/security/permissions.md` §4.1）。没有代登录，也没有支持访问流程。

### 1.2 今天真正接入的平台后台能力

| 组 | 已接入（平台后台） |
|---|---|
| 概览与待处理 | 5 个计数（账户、停用、空间、管理员、未认领历史空间）；历史空间认领 |
| 账户与访问 | 账户列表与筛选；停用；恢复（有缺陷 D1）；撤销全部会话；找回邮件；修改平台角色；工作空间列表与冻结/解冻；组织列表（生命周期操作只对自己拥有的组织开放）；外部身份提供方状态与探测（只读） |
| 应用与业务 | 邮件发件箱，以及失败的重置/验证邮件重试；API 控制台（审批、凭据签发/轮换/停用/启用/撤销令牌、调用记录、契约、调试器、webhook 代理，webhook 需要空间凭据） |
| 运行与数据 | 无 |
| 配置与治理 | 注册策略（唯一能在后台修改的配置）；审计搜索与分页 |

其余能力都在别处，分三类：

- **租户自助**：组织/空间治理、举报处理、复制审批、自定义角色、成员导入、组织审计。
- **命令行**：
  - Docs：`apikey`、`projects`、`jobs`、`webhooks`、`rescan`、`quarantine`
  - Account：`bootstrap`、`app`、`client`、`keys`、`secret`
  - Message：`rescan`、`quarantine`
  - 脚本：`backup`、`restore`、`blob_audit`、`regression`
- **主机**：环境变量、compose、nginx、GitHub Actions。

### 1.3 结构性缺口

1. **只有一个不分职责的平台管理员标志。** 没有权限目录、委派角色、授予来源、期限和范围，也没有"持有≠可授予"的规则和权限解释（§4.1）。所有 `/api/admin` 路由和 API 控制台都在判断同一个布尔值。
2. **配置只在环境变量和主机上。**
   - 服务端读取约 126 个环境键，外加每个外部 IdP 的 6 个动态键。只有 `registration.mode` 能在数据库里动态修改。
   - 多数值在导入时读取，要改就得 root 编辑 `/opt/synwevo/config` 再重建容器；部署密钥不能改配置（`prod/receive.py:1-6`）。
   - §6、§7 点名的管理值（认证节流、摘要间隔、消息/导入上限）是代码常量。
   - 没有配置注册表、历史、回滚和漂移视图。
3. **大量操作只能在命令行或主机上做，且受两条边界约束。**
   - (a) 账户源不能导入 `synwevo_docs` 和 `synwevo_messages`。Docs 重扫、隔离释放、任务队列、协同状态需要组合根适配器，现成的模式是 `srv/app.py:88-109` 的 `DocsControlPlane`。
   - (b) 网页进程只挂载了 `/opt/synwevo/data`。备份、`current.json`、compose、nginx、clamd.conf 都看不到，需要一个只做固定动作的受限主机执行器，不能开放 docker socket 或 shell。
4. **审计不完整。**
   - `_admin` 拒绝请求时不记录；管理读取不记录。
   - 5 个管理写操作在事务提交后才写审计（D8），而且没有前值。
   - 没有导出、保留期和完整性保护。
   - 以下写操作完全没有审计：Docs 生命周期、任务控制、webhook 变更、上传闸门检测、备份/恢复/部署。
   - CLI 的操作者只记录为 OS 用户。
5. **没有统一的操作机制。**
   - 重认证窗口不绑定具体动作；没有影响预览；管理操作没有幂等键。
   - 除空间成员外没有批量操作。
6. **工作台缺基础能力。**
   - 没有分组导航、环境与范围标识、全局搜索、命令面板、对象详情、待处理中心、保存视图。
   - 统计数字没有时间和定义。
7. **后台看不到运行状态。** 没有健康、队列和 worker 视图；`/readyz` 是匿名接口，覆盖也不全。
8. **租户能力强，平台能力弱。** 组织和空间的治理对租户已经相当完整，但平台缺少以下路径：
   - 孤儿对象交接
   - 非拥有者冻结组织
   - 空间删除或归档
   - 跨组织监管

### 1.4 核查中发现的缺陷（按风险排序；标"已复核"的由本文作者再次读码确认，其余来自分区阅读，修复前先复核）

| 编号 | 风险 | 缺陷 |
|---|---|---|
| D1 | 高 | **已修复（本批，仅本地）**：恢复只接受 suspended；未验证邮箱恢复 pending；pending/pending_delete均拒绝，不更改删除期限或借道取消删除。停用清除待完成认证/恢复凭据；恢复不复活旧凭据。证据：`test_admin_operations.py`、`it/test_admin_user_operations.py` 和真实双浏览器停用/恢复。 |
| D2 | 高 | **提升平台角色时不校验目标（已复核）。** `pending`、`suspended` 的账户和应用身份都能被提升。`platform_admins()` 的计数包含应用身份和无法重认证的管理员，而最后管理员保护与 bootstrap 判断都依赖这个计数。<br>**已修复（仅本地，B1-2，schema v27）**：授予要求目标 active、邮箱已验证、不是应用身份、能完成身份确认；`platform_admins()` 与最后管理员保护只计可用的最高管理员。证据：`test_platform_access.py::test_grant_preconditions_close_d2`、`::test_last_super_admin_under_revocation_suspension_and_self_deletion`。<br>**复审补充（仅本地）**：「可用」还要求能完成严格重认证（D10 之前即有口令），只有 Passkey 或 TOTP 的最高管理员不计入，`super_admin` 只授予有口令的账户（409 `target_cannot_reauth`）；Passkey 只计绑定当前 RP ID 的。证据：`::test_a_current_uv_passkey_allows_a_passwordless_super_administrator_to_take_over`、`::test_a_passkey_bound_to_an_earlier_domain_is_not_a_way_to_confirm_identity`。 |
| D3 | 高 | **注册策略 `closed` 与 `invite` 的执行完全相同（已复核）。** 两处都只判断 `!= "open"`（`ac/registration.py:66`、`ac/external.py:580`），所以 `closed` 下邀请仍能创建账号。 |
| D4 | 高 | **API 控制台轮换时写死零过渡期。** `Credentials.tsx:19` 传 `grace_seconds:0`，`Requests.tsx:27` 传 `overlap_s:0`，而后端支持最长 24 h。每次轮换都会立即中断正在运行的集成。 |
| D5 | 高 | **命令行可绕过所有者同意。** `dw/cli.py` 的 `apikey create` 和 `ac/cli.py` 的 `client register --service` 创建的凭据不绑定审批，控制台也不标记。CLI 还能扩大未绑定服务账户的 scope 或换掉空间。 |
| D6 | 高 | **CLI 应用开关绕过服务层。**<br>• `app disable --workspace` 直接删行，不写安全日志（`ac/cli.py:221-226`），从备份恢复时可能被静默重新启用。<br>• `app grant` 不检查成员身份、冻结状态和空间是否启用了该应用。 |
| D7 | 高 | **停用 messages 应用后出现两个写入源。** 停用后 `messages_launch_url()` 返回 None，账户源上旧的 `/api/auth/messaging` 路由重新可写；消息进程却继续服务已有会话。 |
| D8 | 高 | **已修复（本批，仅本地）**：status、claim、freeze/unfreeze、platform-role、revoke-sessions 的业务变更与成功审计同事务；持写锁后复核当前会话/权限。账户停用和会话撤销的前置日志失败使事务回滚。拒绝记录在失败事务外保存。证据：`test_admin_operations.py`、`test_admin_operation_audit_edges.py`、`test_admin_write_races.py`、`test_admin_recovery_safety.py`。 |
| D9 | 高 | **`keys retire` 没有保护。** 可以退役当前正在用的签名密钥，也可以在最长令牌有效期结束前退役。 |
| D10 | 高·部分修复 | B2本地已接入口令及Passkey UV用途重认证，可用最高管理员按当前RP的验证能力计；删除最后验证方式与角色撤销在同一写锁下保护。邮件验证码不能代替证明，TOTP单独严格重认证及管理员必须MFA策略仍未完成。真实签名反向/竞态/审计回滚和Chromium虚拟UV浏览器已有证据；实体设备尚未验证。 |
| D11 | 高 | **停用前不检查唯一拥有者。** 停用账户时不检查对方是否是某组织或空间的唯一拥有者（账户删除会查，`ac/profile.py:71-103`），也没有平台交接路径，可能留下无主的组织或空间。 |
| D12 | 中 | **组织自定义角色的理由被丢弃。**<br>• UI 要求填写理由，但路由既不接收也不存储（`ac/organization_routes.py:191,203,217,229,241`）。<br>• 移除角色时不要求操作者持有该角色的权限（`core/org_roles.py:287-289`）。 |
| D13 | 中 | **举报复核有并发问题。**<br>• 先查后改，更新语句没有 `WHERE status='open'`（`core/messaging.py:699-721`），两人同时复核时后者覆盖前者。<br>• 其他管理者的 `conversation.reported` 待办永远停在 pending。 |
| D14 | 中 | **转换器内存上限高于容器上限。** `CONVERTER_MEMORY_MB` 默认 8192 MB，而 docs 容器的 `mem_limit` 是 2304m（`prod/compose.yaml:69`）。 |
| D15 | 中 | **扫描上限低于上传上限。** 扫描上限 25 MiB（`SYNWEVO_CLAMD_MAX_BYTES`），Docs 上传上限 100 MB，nginx `client_max_body_size` 是 100m。25–100 MiB 的文件会被接收，然后永远停在 `scan_failed/too_large`。 |
| D16 | 中 | **服务账户重新启用不留痕。** 不写安全日志，也不像空间解冻那样返回恢复说明；从旧备份恢复时可能重新应用停用。 |
| D17 | 中 | **`operator` 角色名不副实（已复核）。** 它没有任何能力，把管理员改成 operator 会静默移除全部管理权，而 UI 把它标为"平台运维"（`acweb/Admin.tsx:237`）。<br>**已修复（仅本地，B1-2）**：v27 迁移为无平台权限并写审计，选择器与筛选移除，兼容路由拒绝 `operator`（A3）。 |
| D18 | 低–中 | **没有调用方的代码和虚设路径。**<br>• 无调用方：`Messaging.sweep_unused_attachments`（未发送的附件永不清理）、`messaging_search.rebuild`、`PushSubscriptions.revoke_all_for_user`、`capabilities.clear_probe_cache`。<br>• `ac/notify.py:9` 说"由管理员 outbox 重驱"，但这个路径不存在。<br>• 桥接 outbox 失败 10 次后静默退出处理，也看不到。 |
| D19 | 低 | **有配置绕过 `env.py` 读取。** `SYNWEVO_COLLAB_MAX_FRAME_BYTES`、`SYNWEVO_MESSAGING_*_WINDOW_S`、`*_UI_DIR` 直接读 `os.environ`；`dw/cli.py` 的 `info` 用 `os.environ.get`，会误报。 |
| D20 | 低 | **webhook 与调试器的交互问题。**<br>• 重复点击 webhook 重投会重复创建投递。<br>• 调试器的 query 字段 `JSON.parse` 失败未被处理。 |

## 2. 管理能力覆盖表

### 2.1 概览与待处理

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| O1 管理台外壳 | `/admin` 入口 | 进入、访问保护 | `core/http.py`、`core/platform_access.py`、`ac/admin_routes.py`；敏感响应 no-store | `acweb/admin/AdminLayout.tsx`、`Admin.tsx`、`pages/ApiPlatform.tsx` | 已接入（本批） | 全局搜索、命令面板、完整范围切换和保存视图尚未实现 | 任一有效平台权限；实际视图按具体权限和 step-up | 平台 | 高 | 每请求重读会话/授权；前端复用权限目录 | `browser_admin_review.py`：三语1440/390/320、触屏Drawer/键盘焦点、旧深链、API tab、锁定会话step-up；`test_platform_access.py` 角色矩阵 | 仅本地，未部署 |
| O2 管理台外壳 | 全局搜索、命令面板、对象详情 | 按 ID/引用查找用户、组织、空间、项目、任务、复制请求；执行受权操作 | 无 | 无 | 缺后端 | 按权限过滤的服务端搜索（不索引正文，不返回文档标题）；对象详情（概览、配置、关联、任务、审计）；命令面板只调用已登记操作 | 目标 各 `platform.*.view` | 全局 | 中 | Store 查询 | 审计查看员搜不到账户管理动作；测试文档标题不出现在结果中；未知 ID 无结果 | 未实现 |
| O3 概览 | 平台概览 | 查看 | `GET /api/admin/overview`（`admin_routes.py:261-275`：账户、停用、空间、管理员、未认领） | `acweb/Admin.tsx:108-144` | 已接入 | 无数据时间、定义与下钻；缺待验证、待删除、失败邮件、挑战故障、封禁地址、待审批、最近管理变更；admins 计数含应用身份（D2） | PA | 全局 | 低 | users/workspaces COUNT | 在隔离实例停用账户后刷新，计数与筛选列表一致；每个新指标下钻总数相同 | 已上线 |
| O4 概览 | 运行与通信健康摘要 | 查看、下钻 | 仅各角色匿名 `/readyz`（`core/http.py:1132-1230`：database、storage、worker、malware_scanner、messaging 计数）；邮件积压见 `/api/admin/mail` | 无 | 缺后端 | 汇总邮件积压/失败、通知与推送投递、开放举报、桥接积压/死信、消息流连接、失败 webhook 订阅、Docs 队列、worker 存活；进程不可达显示"未接入"而非 0；明细见 R16–R18 | PA | 全局（每角色） | 低 | 只读聚合 + 读取固定源站 `/readyz` | 造一封失败邮件与一条开放举报，计数变化并能下钻到对应列表 | 未实现 |
| O5 待处理 | 治理待办中心 | 查看并跳到对象 | 仅 overview 的 `unclaimed` | `Admin.tsx:126-131` ClaimCard | 缺后端 | 无有效拥有者的组织/空间、临近清除的已删组织、待决复制与 API 审批、停滞导入、失败 webhook 与桥接死信、隔离待复核、卡住任务；"已解决"指对象状态改变，不是已读 | PA | 全局 | 低 | 只读查询 | 每个计数等于下钻行数，底层修复后才归零 | 未实现 |
| O6 待处理 | 未认领历史空间 | 认领给已有账户 | `POST /api/admin/workspaces/{wid}/claim`（`admin_routes.py:319-393`，事务内复检，单一拥有者） | `Admin.tsx:146-175` ClaimCard | 已接入 | 账户选择器含应用身份；UI 固定 owner 且隐藏可选 name；审计在提交后写（D8）。B1-2 复审（仅本地）：需要 `platform.workspaces.claim`（仅最高管理员）；事务内拒绝本人（`self_claim`）与持有平台授予的账户（`target_is_platform_admin`）；选择器只列无平台权限的账户，卡片只对能指派且能搜索账户的人显示（`test_platform_access.py::test_the_historical_data_goes_only_from_a_super_administrator_to_an_ordinary_account`、`admin-access.test.mjs`） | PA! | 空间 | 高 | memberships、workspaces、workspace_apps、app_entitlements 同一事务 | `ac-tests/test_admin.py:127-198,347-367`（并发下单一拥有者；管理员仍读不到文档） | 已上线 |

### 2.2 账户与访问

#### 平台管理员、权限与强认证

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A1 平台权限 | 平台角色（5 个内置角色） | 授予 | `POST /api/admin/users/{uid}/platform-grants {role, reason, expires_at?}`；兼容的 `POST /api/admin/users/{uid}/platform-role`（admin → 授予 super_admin，none → 撤销全部，operator → 400 `operator_retired`）。两者都经 `core/platform_access.py` `PlatformAccess.grant`：一个 `BEGIN IMMEDIATE` 事务内复读操作者权限与目标前置条件，写授予、同步 `users.platform_role` 镜像、收紧会话、写审计 `admin.platform.grant`（前后角色集合、理由、请求 ID） | Users 行菜单「平台权限」对话框（`acweb/governance/PlatformAccess.tsx`）；「平台管理员」标签 | 已接入 | 无影响预览与目标通知；接任验证属批次 2 交接；撤销现已写前置安全日志和事务收据，旧备份不能静默复原相同授予；无日志恢复仍缺安全核对门禁（R21） | `platform.roles.assign`，`super_admin` 另需 `platform.roles.assign_super`；严格重认证 + 理由 | 全局 | 高 | platform_role_grants；users.platform_role 镜像；sessions（收紧到管理员闲置时限、取消保持登录） | `services/identity/tests/test_platform_access.py`：`test_a_super_administrator_grants_and_revokes_through_the_routes`、`test_grant_preconditions_close_d2`、`test_holding_is_not_granting_and_nobody_changes_their_own_authority_to_assign`、`test_a_grant_tightens_the_grantees_sessions_and_a_revocation_applies_on_the_next_request`、`test_a_current_uv_passkey_allows_a_passwordless_super_administrator_to_take_over`（`super_admin` 需口令或当前RP的UV密钥）、`test_expired_grants_are_ignored_and_can_be_granted_again`（审计记下被替换的到期授予） | 仅本地 |
| A2 平台权限 | 平台角色 | 撤销，最后可用最高管理员保护 | `POST /api/admin/platform-grants/{gid}/revoke {reason}` → `PlatformAccess.revoke`：同一事务内计数可用最高管理员（active、计入、能完成身份确认与严格重认证；应用身份、停用/删除/待验证账户、只有TOTP、没有可用严格验证方式的账户与到期授予不计），归零时 409 `last_super_admin`；停用路由对最后一名返回 `last_super_admin`、对其他持有授予者返回 `demote_first`；本人删除（`ac/profile.py`）同样检查 | 同上 | 已接入 | 撤销不通知目标；本批 `platform_grant_revoked` 保护旧备份撤权，旧恢复工具须升级（见使用说明）；无日志恢复仍缺门禁 | 同上 | 全局 | 高 | 同上；权限每个请求重读，撤销在下一请求生效 | `services/identity/tests/test_platform_access.py`：`test_last_super_admin_under_revocation_suspension_and_self_deletion`、`test_two_super_admins_revoking_each_other_at_once_leave_exactly_one`（两人同时互撤只成功一个）、`test_a_current_uv_passkey_allows_a_passwordless_super_administrator_to_take_over`（可用 = 能严格重认证）、`test_the_audit_names_what_a_change_did_even_on_an_account_whose_grants_do_not_count`、`test_a_finalized_deletion_records_each_grant_it_ends`、`test_a_replayed_deletion_ends_the_grants_with_the_account`；`test_self_service.py`：`test_last_platform_admin_cannot_delete`、`test_concurrent_last_admin_deletions_cannot_remove_both`；`test_identity.py::test_last_platform_admin_cannot_be_demoted` | 仅本地 |
| A3 平台权限 | 平台角色 operator（「平台运维」） | 停用 | v27 迁移与每次打开库的对账（`core/platform_access.py` `reconcile_mirror`）把 `operator` 改为 `none` 并写审计 `admin.platform.operator_cleared`；兼容路由对 `operator` 返回 400 `operator_retired`；`users.platform_role` 的 CHECK 保留，不重建表 | 选择器与筛选中已移除（`role=operator` 筛选为 422），旧行显示为「无平台权限」 | 已接入 | — | — | 全局 | 低 | 迁移与对账 | `services/identity/tests/test_platform_access.py`::`test_migration_26_to_27_makes_admins_super_admins_and_clears_operators`；`test_admin.py::test_user_filters_and_counts_apply_before_pagination`；`test_admin_governance.py::test_workspace_org_owner_and_operator_cannot_enter_platform_governance` | 仅本地 |
| A4 平台权限 | 权限目录、内置角色、授予（查看 / 管理 / 支持 / 授权；期限） | 定义、授予、撤销、列出有效权限 | `core/platform_access.py`：18 项权限（B1-2 复审新增 `platform.workspaces.claim`）、5 个内置角色（`super_admin` 按定义等于整个目录，其余逐项列出）、`platform_role_grants`（schema v27；`(user_id, role)` 部分唯一索引；角色 CHECK）。`require_platform` 用于全部 `/api/admin` 路由与 `/admin` 外壳，`require_manage` 查 `api_platform.manage`；`describe_user` 新增 `platform_permissions`、`platform_roles` | 标签按调用者权限显示（`acweb/Admin.tsx`）；标签内的写操作按权限禁用并写明原因（`acweb/governance/access.ts`）；审计筛选含「平台权限」与「被拒绝的管理请求」；门户与账户顶栏按任一平台权限显示「平台管理」 | 已接入 | 作用域只有全局（组织/应用范围、可激活授予留待后续）；没有自定义角色；异步任务里还没有平台权限执行点；导出与敏感内容权限尚未登记 | `platform.roles.*` | 全局 | 高 | 授予表，每个请求重读 | `services/identity/tests/test_platform_access.py`：`test_each_built_in_role_is_allowed_exactly_its_permissions`（5 个角色 × 全部管理路由与 API 控制台）、`test_every_admin_route_is_in_the_matrix`、`test_no_platform_permission_sees_404_everywhere_including_owners_and_workspace_admins`、`test_the_admin_shell_opens_for_any_platform_permission`、`test_a_denied_attempt_is_audited_once_per_window`、`test_expired_grants_are_ignored_and_can_be_granted_again`、`test_the_rollback_mirror_follows_every_grant_and_carries_a_rollbacks_changes_forward`、`test_a_lower_administrator_does_not_act_on_a_higher_one`（`target_outranks_actor`，审计限流）；`services/identity/web/tests/admin-access.test.mjs`（页面按角色提供的操作与服务器一致） | 仅本地 |
| A5 平台权限 | 权限解释与模拟 | 说明为何允许或拒绝 | `GET /api/admin/users/{uid}/platform-access`：每项权限及其来源授予、每个授予是否计入、调用者可授予的角色与可撤销的授予，以及不能授予（`assign_refusal`）或不能撤销（每个授予的 `revoke_refusal`）的原因代码；`GET /api/admin/session`：调用者的有效权限与来源、step-up 状态、重认证剩余秒数、实例标记（与 `/healthz` 同源的 `instance_kind`） | 「平台权限」对话框的「有效权限」，不能撤销或授予的原因以可见文字写在按钮旁（确认框叠在对话框之上，关闭后焦点回到原按钮）；系统写的理由按读者语言显示；管理页顶栏在测试实例上显示「测试实例」 | 已接入（解释）；缺后端（模拟） | 受控模拟（不执行业务、不泄露无权对象） | `platform.roles.view`；session 只需任一平台权限（锁定会话也可读） | 全局 | 低 | 只读计算 | `services/identity/tests/test_platform_access.py`：`test_session_roster_and_explanation`、`test_a_super_administrator_grants_and_revokes_through_the_routes`；`test_email_code_login.py`（锁定管理员读到 `step_up.required=true`） | 仅本地 |
| A6 平台权限 | 平台管理员名册 | 查看授予来源、能否完成身份确认、最近登录 | `GET /api/admin/platform/roles`：目录与名册（授予、来源、到期、是否计入、口令 / 可验证 Passkey / TOTP、能否严格重认证、最近一次中央登录） | 「平台管理员」标签（`acweb/governance/PlatformAccess.tsx`）；少于 2 名可用最高管理员时常驻「建议至少 2 名」 | 已接入 | 会话列表与 MFA 普及统计（A32）；最近登录只读保留的会话行（结束一周后清理） | `platform.roles.view` | 全局 | 中 | platform_role_grants、sessions、user_passwords、passkeys、totp_secrets | `services/identity/tests/test_platform_access.py`::`test_session_roster_and_explanation` | 仅本地 |
| A7 最高管理员恢复 | 首位管理员 | 一次性令牌初始化 | `bootstrap token --ttl`限定60–3600秒；`POST /api/auth/bootstrap`在写锁内重新检查可用管理员和令牌，账户/个人空间/消费/授予/成功审计同事务；CLI同样原子提交 | 登录页受控领取 | 已接入 | 完整应急恢复仍见A8 | 主机生成、令牌领取 | 全局 | 高 | 唯一Store，bootstrap_tokens | `test_bootstrap_transactions.py`覆盖并发领取、有效期穿越、授予/审计故障回滚、会话交付失败后可重新登录、CLI二次检查 | B2仅本地 |
| A8 最高管理员恢复 | 应急恢复（break-glass） | 在主机上创建新管理员 | `ac/cli.py:113-141` `bootstrap admin --email … --force`（审计 system `cli.bootstrap.admin`，`cli.py:52-71`） | 无 | 仅命令行 | 只能新建账户，不能提升或修复已有账户；无时限、演练记录、事后轮换和对账视图；依赖主机数据库访问 | 主机 | 全局 | 高 | 直接 `Identity.create_user` | 在隔离副本上把全部管理员降级或锁定后运行：新管理员能登录、step-up 并恢复他人；审计含 os_user 与 host | 已上线 |
| A9 强认证 | 管理员step-up | 要求和完成近期证明 | `reauth_routes.py`读取实际方式；口令或当前RP的UV密钥验证，在原会话追加已证明因素 | `AdminStepUp.tsx`及账户安全面板 | 已接入 | 管理员必须MFA策略未完成；仅口令仍按既有规则可管理 | 有效平台角色及会话 | 全局 | 高 | sessions.amr/reauth_scope与成功审计同事务 | 邮件码会话锁定、口令及真实签名Passkey UV证明；错误/撤销/审计失败不解锁 | B2仅本地 |
| A10 强认证 | 危险动作重认证 | 近期严格用途证明 | `reauth_routes.py`、`Passkeys`、`Sessions.confirm_reauth`；独立一次性挑战绑定用户/中央会话/admin或mfa/RP/origin/时限，服务端要求UP及UV | `ActionDialog.tsx`按真实可用方式呈现，SPA卸载取消待处理证明 | 已接入 | 用途证明仍可在窗口内用于同类动作；账户停用另绑定目标/参数/版本预览，其余危险动作的独立影响预览仍待补齐 | 对应平台权限+严格重认证+理由 | 全局/目标 | 高 | 原sessions行，无新登录或Cookie；默认600秒窗口 | `test_passkey_reauth.py`真实签名反向、竞争、审计故障；浏览器11条链路与三语手机截图 | B2仅本地 |
| A11 强认证 | 第二因素要求策略（管理员 / 组织 / 全体） | 设置与执行 | 无；现有规则只有 admin_capable，以及管理员和 TOTP 用户的 Passkey 须做用户验证（`auth_routes.py:346-352`） | 无 | 缺后端 | 策略存储；登录与 step-up 时执行；注册宽限期；防止唯一管理员陷入无法完成的 MFA 循环（§4.2、§7.11） | 目标 `platform.security.manage` | 全局 | 高 | — | 开启"管理员须 MFA"后，仅口令的会话被锁，直到完成 TOTP 或 Passkey；普通用户不受影响；最后一名无 MFA 管理员被引导去注册，而不是被锁死 | 未实现 |

#### 用户与账户

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A12 用户 | 账户列表 | 列表、搜索、筛选 | `GET /api/admin/users`（`admin_routes.py:396-417`，按 q/status/role，每页 ≤200，快照分页） | `Admin.tsx:177-219` | 已接入 | 缺邮箱验证、最近登录、MFA/Passkey/IdP 列，缺排序和保存视图；应用身份被当作人列出；`status=deleted` 可选但恒为空；sessions 计数含依赖方会话行 | PA | 全局 | 低 | users、memberships、sessions | `test_admin.py:404-443`；补测：应用身份不出现，pending 只含未验证账户 | 已上线 |
| A13 用户 | 账户详情与安全时间线 | 查看资料、验证状态、登录方式、成员关系、组织、会话、退避状态、近期事件 | 没有 `GET /api/admin/users/{uid}`；可用零件：`core/sessions.py:260-282`、`ac/external.py:375`、`ac/mfa.py:214`、audit_events、auth_attempts | 无（只能在审计里按邮箱搜） | 缺后端 | 只返回元数据的详情接口与视图（不含口令哈希、TOTP 种子、恢复码、令牌）；关联对象与审计时间线 | 目标 `platform.users.view` | 对象 | 中 | — | 详情与本人账户页一致；响应中没有秘密字段；失败登录出现在时间线 | 未实现 |
| A14 用户 | 账户 | 停用 | `POST /api/admin/users/{uid}/status-preview` 与 `/status` → `ac/admin_operations.py` | 账户行菜单，影响预览/理由/重认证/持久结果 | 已接入（本批） | 批量、通知、唯一拥有者诊断/交接（D11）；机器身份按原有独立授权控制 | `platform.users.manage` + 严格重认证 | 平台/单账户/当前源站 | 高 | users、sessions、委托与待完成证明、audit_events、admin_operations 同一Store事务；user_suspended前置日志+收据 | `test_admin_operations.py` 绑定/并发/幂等/故障；`it/test_admin_user_operations.py` 真实Docs会话与委托拒绝、跨空间/内容边界；浏览器双context前后验证 | 仅本地，未部署 |
| A15 用户 | 账户 | 恢复 | 同一已确认操作接口，只允许 suspended；原邮箱未验证恢复 pending | 同上，结果说明旧凭据不恢复 | 已接入（本批） | 完整交接与孤儿治理（A37）；不提供取消删除的旁路 | 同A14；不能作用于权限更高者 | 平台/单账户 | 高 | users.status；不重新签发或恢复旧访问凭据 | `test_admin_operations.py` 拒绝pending/pending_delete/应用身份、恢复保留邮箱验证；跨模块与双浏览器：旧会话/令牌仍拒绝、新登录有效 | 仅本地，未部署 |
| A16 用户 | 会话与令牌 | 全部撤销 | `POST /api/admin/users/{uid}/revoke-sessions`，持写锁复核权限 | 账户行菜单 | 已接入（本批加固） | 尚未提供逐设备清单、单设备管理员撤销、完整影响预览/幂等目录 | `platform.users.support` + 严格重认证，禁止对更高权限者操作 | 单账户 | 高 | 会话/委托/未完成认证证明与审计同事务，sessions_revoked日志+收据 | 管理员回归；安全日志恢复回归验证旧备份不能复活登录证明 | 仅本地加固，未部署 |
| A17 用户 | 会话/设备、Passkey、外部身份绑定、推送设备 | 按用户列出；撤销单个设备；移除 Passkey；解绑外部身份；撤销全部推送订阅 | 服务已有，但只供本人使用：`sessions.py:221-244` revoke_device、`ac/provider_routes.py:407`、`ac/passkeys.py:103-104`、`ac/external.py:699-721`（含最后登录方式检查 `:445`）、`core/push.py:181`（无调用方） | 无（只有会话数列） | 缺界面 | 管理只读接口；危险动作需理由、通知和安全日志；IP 属个人数据，谨慎展示 | 目标 `platform.users.manage` | 对象 | 中 | sessions、client_sessions、passkeys、external_identities、push_subscriptions | 撤销两台设备之一：该上下文 401，另一台仍可用；被移除的 Passkey 无法登录；解绑后回调不再登入该账户 | 已上线（服务） |
| A18 用户 | 账户 | 批量停用、批量撤销会话 | 无 | 无（`acweb/governance/BulkResult.tsx` 只用于空间成员） | 缺后端 | 服务端快照目标、逐项结果、幂等、跳过持有平台权限者、取消与重试（§5.3） | 目标 `platform.users.manage` | 全局 | 高 | — | 只停用筛选快照列出的 ID；超时重试不重复写安全日志；集合中的管理员被报告为拒绝 | 未实现 |
| A19 用户 | 账户邮箱与资料 | 支持更正 | 只有本人能改：`ac/account_routes.py:166-217`（换邮箱需重认证）、`:83-90` | 无 | 缺后端 | 新地址必须由持有人验证；撤销会话；通知新旧两个地址；审计 | 目标 `platform.users.manage` | 对象 | 中 | users.email、email_key | 新地址确认前不能登录；旧地址收到通知 | 未实现 |
| A20 用户 | 删除请求（pending_delete，7 天宽限） | 查看到期时间与阻塞约束、取消、处理终结失败 | 本人申请 `account_routes.py:259-285`、本人取消 `:307-316`；每小时终结 `ac/app.py:47-55` → `ac/profile.py:125-153`；约束 `profile.py:72-103` | Users 筛选 pending_delete（`Admin.tsx:196`） | 缺后端 | 列表不显示 deletion_due_at、约束和终结失败标记（meta `account-deletion-journal:*`）；目前只能通过有缺陷的"恢复账户"取消（D1）；是否允许代为取消由政策决定 | PA | 对象 | 高 | users.status、deletion_due_at + 账户 Sweeper | 测试账户申请删除后，后台显示到期时间；到期后变为 deleted，凭据被移除，platform_role 为 none | 已上线（部分） |
| A21 用户 | 平台级邀请 | 邀请新人加入平台 | 无（空间邀请属于空间 owner/admin，`auth_routes.py:530-545`；权限文档写明平台管理员没有邀请权） | 无 | 缺后端（待决策） | 是否需要由所有者决定；若需要，复用 Invites 并与注册策略配合，不设默认口令 | 目标 `platform.users.invite` | 全局 | 中 | — | 受邀地址在 invite/closed 模式下可注册，其他地址仍返回 registration_closed | 未实现 |
| A22 用户 | 应用身份（`app_accounts`，例如 Docs 卡片发送者） | 排除出人员治理 | `core/messaging_bridge.py:142-175`（无口令的有效 users 行；`sessions.py:117-123` 拒绝为其建会话） | 与人一起出现在账户列表和认领选择器中 | 缺后端 | 列表、停用、提升、找回、认领都不排除它们；被提升后会抬高 `platform_admins()` 的计数 | 与普通账户相同 | 对象 | 中 | users + app_accounts | 应用身份不出现在 `/api/admin/users`；对其做 platform-role、status、recovery 返回 409 | 已上线（问题在线上） |

#### 验证、找回与登录方式

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A23 找回 | 口令找回邮件 | 管理员发起 | `POST /api/admin/users/{uid}/recovery`（`admin_routes.py:191-199` → `_dispatch :170-189`：仅 active/pending；每用户每小时 3 次且间隔 60 s；每次生成新令牌并进入 outbox） | Users 行菜单"发送找回邮件" | 已接入 | 管理员看不到链接（正确）；对丢失第二因素无效（重置保留 TOTP）；file 模式下不真实发送 | PA! | 对象 | 中 | email_tokens + mail_outbox worker | `test_admin_governance.py:107-122,259-272`：入队、响应不含令牌、链接只能用一次、第 4 次 429 | 已上线 |
| A24 找回 | 验证邮件 | 管理员直接重发 | 辅助函数支持 kind=verify（`admin_routes.py:176-189`），但只能经"重试失败邮件"触达 | 仅邮件标签页的失败重试 | 缺界面 | 对 pending 账户直接重发的路由与界面（限流与审计相同） | 目标 `platform.users.support` | 对象 | 低 | email_tokens + mail_outbox | 对 pending 测试账户重发只入队一封；链接要等邮箱持有人设置新口令后才激活账户 | 已上线（服务） |
| A25 找回 | 邮箱已验证状态 | 手工标为已验证 | 无（正确地不存在） | 无 | 不适用 | §7.2 禁止；但 D1 目前能达到等效的绕过，须在批次 1 关闭 | — | 对象 | 高 | — | 没有任何管理接口写 users.email_verified_at；对 pending 账户设 status=active 被拒 | — |
| A26 找回 | 第二因素（TOTP 与恢复码都丢失） | 身份确认后的受控重置 | 无；本人关闭需要当前验证码（`auth_routes.py:1056-1068`） | 无 | 缺后端 | 身份证明、理由、延迟与通知、撤销会话与令牌、安全日志；全程不读取种子（§4.2、§4.3、§7.2） | 目标 `platform.users.recover`（高风险） | 对象 | 高 | 将作用于 totp_secrets、recovery_codes、passkeys | 恢复后能用口令登录并重新注册 TOTP；旧种子失效；会话全部撤销；有审计与通知 | 未实现 |
| A27 诊断 | 注册与机构（CUHK）邮箱 | 分阶段排查 | 各阶段分散在不同位置：<br>• 校验 `identity.py:53-68`，无审计<br>• 挑战 `core/challenge.py:175-207`<br>• 限流 `ac/registration.py:55-62`，静默返回同样答复，无审计<br>• 建行与 `auth.registration`<br>• 验证令牌<br>• `mail_outbox`<br>• 链接使用<br>• `auth.email.verified` | 部分：审计按邮箱搜索、邮件标签按收件人查 | 缺后端 | 按地址或用户串起全部阶段的诊断接口（校验→挑战→限流→建行→令牌→发件箱→服务商结果→激活），不暴露令牌；被限流的注册写审计 | PA | 对象 | 中 | audit_events、users、email_tokens、mail_outbox | 用隔离实例和文件邮件（`services/identity/scripts/ui_e2e_institutional_mail.py`）注册 CUHK 形式的地址：每个阶段都有时间；被限流的尝试显示"已限流" | 未实现 |
| A28 登录方式 | 外部身份提供方 | 查看配置状态、探测 discovery | `GET /api/admin/identity-providers[?probe=1]`（`admin_routes.py:250-259` → `ac/external.py:174-222`，脱敏，不返回密钥） | `acweb/governance/IdentityProvidersTab.tsx:49-96` | 已接入 | 没有按提供方的成功/失败统计；探测结果不保存 | PA | 全局 | 低 | 启动时由环境构建（`ac/app.py:170-172`） | 配置了 OpenID 测试替身的隔离实例：显示已配置、回调 URI 和绑定数；探测可达；未配置的提供方列出缺失变量名 | 已上线 |
| A29 登录方式 | 外部身份提供方配置（`SYNWEVO_IDP_*`） | 启用、修改、轮换密钥、停用 | 启动时读环境（`external.py:113-150`；半配置会拒绝启动） | 无（标签页写明不能在此设置） | 仅主机 | 修改需要主机环境 + 重建账户服务 + 在服务商控制台登记；没有漂移或轮换健康视图 | 主机 | 全局 | 高 | 账户服务进程启动时读取 | 重启后 `/api/auth/external/providers` 恰好列出已配置项，并能对服务商或测试替身完成登录 | 已上线（值待核实） |
| A30 登录方式 | 邮件验证码登录 | 开关、限额 | `ac/email_login.py:34-43` 常量；路由总是挂载（`ac/app.py:122-124`） | 无 | 缺后端 | 没有开关和策略；管理员看不到它是否开启 | 目标 `platform.security.manage` | 全局 | 中 | email_login 路由 | 关闭后 `POST /api/auth/login/email/start` 被拒，登录页隐藏入口 | 未实现 |
| A31 登录方式 | Passkey RP（`SYNWEVO_RP_ID`、`RP_ORIGINS`）；口令策略与 Argon2id 参数；服务端根密钥（`SYNWEVO_SECRET`，用于封存 TOTP 种子和 webhook 密钥） | 只读展示；根密钥换钥 | `ac/passkeys.py:45-70`；`core/passwords.py:40-53,192-196`；`ac/cli.py:439-557`（`secret status`、`secret rekey`、`info`）；`core/auth.py:231-265`（KCV 不符拒绝启动） | 无 | 仅命令行 | 需要只读展示：RP ID 与源站、绑定在别处的 Passkey 数；口令下限（单因素 15 位，有 MFA 8 位）；根密钥来源、KCV 是否匹配、封存行数（不显示值）。换钥保持为主机步骤 | 主机 | 全局 | 高 | 各角色环境 + meta.server_secret/root_kcv | 显示值等于 `passwords.parameters()` 和 `cli secret status` 的输出；换钥后 TOTP 与 webhook 签名仍验证通过 | 已上线（值待核实） |
| A32 登录方式 | 第二因素普及情况 | 统计 TOTP、Passkey、剩余恢复码 | 无 | 无 | 缺后端 | 在概览和管理员名册上显示聚合数，不含逐码数据 | PA | 全局 | 低 | totp_secrets、passkeys、recovery_codes | 与隔离实例上实际注册的数量一致 | 未实现 |

#### 组织、工作空间与成员

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| A33 组织 | 组织 | 平台列表、搜索、按状态筛选 | `GET /api/admin/organizations`（`admin_routes.py:221-238`，逐行给出成员/空间计数和调用者角色） | `acweb/governance/GovernanceTabs.tsx:78-106` | 已接入 | 缺拥有者和有效拥有者列（发现不了孤儿组织）、邀请策略、创建者；非成员管理员点不进详情；逐行 COUNT（N+1） | PA | 全局 | 低 | organizations、organization_memberships、workspaces | `test_admin_governance.py:168-180` | 已上线 |
| A34 组织 | 组织 | 平台只读详情（拥有者、管理员、空间、设置、自定义角色、待决导入与审批、有效权限解释） | 只有成员能读：`core/organizations.py:169-203,272-277,325-358`（非成员 404）；`core/org_roles.py:135-147` effective | 无（`GovernanceTabs.tsx:97` 只在管理员本身是成员时给链接） | 缺后端 | 在明确的平台权限下返回治理元数据的接口；不创建成员关系，不含文档内容 | 目标 `platform.orgs.view` | 组织 | 中 | organizations、organization_memberships、organization_roles、member_imports、project_copies | 非成员平台管理员通过 `/api/admin` 看到详情；`/api/auth/organizations/{oid}` 对其仍返回 404；Docs 仍拒绝访问（`it/test_members_access.py:149-160`） | 未实现 |
| A35 组织 | 组织生命周期 | 冻结、解冻、软删除（7 天）、恢复——由非拥有者的平台管理员操作 | `POST /api/admin/organizations/{oid}/{action}`（`admin_routes.py:240-247`）委托给要求组织拥有者身份的 `Organizations.transition`（`organizations.py:484-515`，`:487`） | 组织标签只在管理员是拥有者时显示按钮（`GovernanceTabs.tsx:98`） | 缺后端 | 平台作用域路径：可委派权限、严格重认证、理由、影响预览（会话、令牌、邀请、审批、运行中任务）、不建成员关系、审计标注平台操作者。今天平台能逐个冻结空间，却不能冻结组织 | 现为组织 owner + PA!；目标 `platform.orgs.manage` | 组织 | 高 | 复用 transition 状态机 + `_revoke_workspace_authority` + 安全日志 | 非拥有者平台管理员冻结组织：成员在下一请求失去访问，审计显示平台操作者，管理员仍没有成员关系和 Docs 访问（现有测试 `test_admin_governance.py:174,214` 断言今天返回 404/403） | 已上线（仅拥有者路径） |
| A36 组织 | 已删组织 | 7 天后永久清除 | 无（`organizations.py:493-500` 有 recover_until，但没有清除任务；`profile.py:80,95` 把过期的视为已消失） | 无 | 缺后端 | 定义清除作业：对空间、项目、blob、导入、复制、消息做引用检查，说明备份保留；预览；严格批准；区分归档、软删除、清除 | 目标 `platform.orgs.purge` | 组织 | 高 | 将为 Sweeper 或管理作业 | 清除后组织和空间不在列表中；blob 只在无引用时回收；审计与安全日志保留 | 未实现 |
| A37 组织/空间 | 组织与空间所有权 | 平台受控交接（没有有效拥有者时的孤儿恢复） | 无：<br>• 管理员停用账户时不查唯一拥有者（D11）<br>• 平台认领只覆盖历史 pending_claim 空间 | 无 | 缺后端 | 停用前检查是否为唯一拥有者；从现有有效成员中指定拥有者（接任者近期完成 MFA、理由、审计、不开放内容访问）；"没有有效拥有者"清单 | 目标 `platform.orgs.manage` | 组织/空间 | 高 | 与 `Identity.transfer_ownership` 相同的单事务写（`identity.py:586-618`） | 停用唯一拥有者后，后台显示"没有有效拥有者"；交接后该成员能管理成员；审计记录平台操作者与理由 | 未实现 |
| A38 组织 | 组织自助治理：创建、设置与邀请策略、头像、转让、成员、导入、复制审批、组织审计 | 由组织角色自助完成 | `ac/organization_routes.py`：<br>• `:52-55` 创建，每天 10 个<br>• `:61-65` 设置<br>• `:81-108` 头像<br>• `:119-127` 审计<br>• `:129-163` 成员<br>• `:262-283` 转让与生命周期<br>另有 `ac/member_import_routes.py:312-430`、`ac/approval_routes.py:52-92` | 租户界面 `acweb/pages/org/*`；`/admin` 中没有 | 已接入（租户） | 平台没有对应路径（见 A34、A35、A37）；导入确认的理由可选，上传、映射、取消不需要重认证；平台管理员按设计不是复制审批人 | 租户：org.* 权限 + 组织处于活跃状态；敏感写需严格重认证 + 理由 | 组织 | 高 | organizations、organization_memberships、member_imports、project_copies；导入 runner 线程；Docs CopyRunner | `ac-tests/test_org_roles.py`、`test_member_import.py`、`test_org_audit.py`；`it/test_member_import_engine.py`、`test_organization_restore.py` | 已上线·本地有改动（导入路由、`OrganizationApprovals.tsx`） |
| A39 组织 | 组织自定义角色 | 创建、编辑、复制、删除；分配 | `core/org_roles.py:207-299`；`organization_routes.py:189-252` | 租户界面 `OrganizationRoles.tsx:81-84`、`OrganizationMembers.tsx:57` | 已接入（租户）·有缺陷 | D12：理由被丢弃，移除角色不要求持有其权限；没有平台角色模板 | 租户：org.roles.manage + 持有涉及的全部权限 + 严格重认证 | 组织 | 中 | organization_roles、organization_role_assignments，每次权限检查时读取 | `ac-tests/test_org_roles.py`；补测：理由写入 audit meta | 已上线 |
| A40 组织 | 组织创建策略 | 规定谁可以建组织 | 无：任何有效账户都能建（`organization_routes.py:52-55`） | 无 | 缺后端 | 策略（开放 / 已验证邮箱 / 平台批准 / 关闭），在 `Organizations.create` 执行，写审计，并在创建页说明 | 目标 `platform.config.manage` | 全局 | 中 | 将由 Organizations.create 读取 | 策略关闭时 `POST /api/auth/organizations` 返回拒绝码，表单给出说明 | 未实现 |
| A41 空间 | 工作空间 | 平台列表、搜索 | `GET /api/admin/workspaces`（`admin_routes.py:297-308`，给出成员、项目、应用计数） | `Admin.tsx:239-269` | 已接入 | 不能按状态、类型、组织筛选，没有组织列和拥有者；已返回的启用应用没有渲染；没有存储用量；N+1 | PA | 全局 | 低 | workspaces、memberships、projects、workspace_apps | 只返回计数，从不返回项目名（`test_admin.py:95,472`） | 已上线 |
| A42 空间 | 工作空间成员 | 平台只读 | `GET /api/admin/workspaces/{wid}/members`（`admin_routes.py:310-317`） | 无（只在管理员本身是 owner/admin 时链接到租户页，`Admin.tsx:259`） | 缺界面 | 空间详情视图；分页（今天不分页）；应用资格列；未知空间返回 404；读取邮箱应写审计 | PA | 空间 | 中 | memberships JOIN users | 管理员看到成员、角色、can_export，但不获得成员关系；普通账户 404（`it/test_members_access.py:155`） | 已上线（接口） |
| A43 空间 | 工作空间 | 冻结、解冻（平台停止开关） | `core/identity.py:722-747`；`POST /api/admin/workspaces/{wid}/freeze`、`/unfreeze`（`admin_routes.py:486-520`：冻结写安全日志，解冻返回恢复说明） | `Admin.tsx:258,264-267` | 已接入 | 没有影响预览（成员、运行任务、密钥、webhook）、批量、期限或工单；审计在提交后写；冻结时停用的 webhook 订阅在解冻后不会自动恢复 | PA! | 空间 | 高 | workspaces.status，每请求由 `core/http.py:372-396`、`core/authority.py:118-158`、`core/machine.py:175-178`、`dw/webhooks.py:764-769` 读取 | 冻结后成员请求 403 workspace_unavailable；机器调用和延期任务被拒（`it/test_members_access.py:127`）；审计只含计数（`test_admin.py:369`） | 已上线 |
| A44 空间 | 工作空间 | 重命名、归档/恢复、软删除/恢复、永久清除、移出或移入组织 | 无：<br>• status CHECK 允许 deleted，也有 deleted_at 列（`core/store.py:135-142`），读取方也已遵守（`core/http.py:385-390`），但没有任何代码写入<br>• organization_id 只在 `organizations.py:241,321` 两处写 | 无 | 缺后端 | 定义各状态的语义（只读还是不可访问）；处理运行任务、分享链接、邀请和成员；恢复窗口；清除前检查 Docs 租户数据的引用；移动时对账成员（仅在产品需要时） | 目标 `platform.workspaces.manage` | 空间 | 高 | 将写 workspaces.status、deleted_at、name | 已删除的空间处处被拒（403/410）；窗口内可恢复；清除后 blob 回收有证据；重命名后切换器和邀请邮件显示新名 | 未实现 |
| A45 空间 | 空间成员、邀请、应用资格（租户） | 列表；改角色和导出权限；移除；邀请（单个或批量）、重发、撤销；授予或撤销应用资格；转让所有权 | `ac/members.py:197-631`；`auth_routes.py:530-589`；`ac/invites.py:77-133`（每空间每天 50 个，同一地址间隔 60 s）；`core/identity.py:498-720` | 租户界面 `acweb/governance/MembersPanel.tsx`、`InvitesPanel.tsx`、`dialogs.tsx` | 已接入（租户） | 平台没有路径；撤销邀请不需要理由；授予资格的理由可选；一页只显示一个应用 | 租户：空间 admin/owner，在角色上限内；升降 owner/admin 需严格重认证 + 理由 | 空间 | 高 | memberships、invitations、app_entitlements、oauth_tokens、sessions；安全日志 | `ac-tests/test_members.py`；`it/test_members_access.py:95,115` | 已上线 |
| A46 空间 | 空间成员 | 不经邀请直接添加 | `Identity.add_member`（`identity.py:430-464`）只是运维或测试夹具用的原语，没有 HTTP 或 CLI 路由 | 无 | 不适用 | 故意不做：成员关系必须由本人接受邀请产生 | — | 空间 | 高 | — | 除接受邀请、认领和所有权流程外，没有路由写 memberships | — |
| A47 空间 | 邀请 | 平台监管：搜索待决、过期或发信失败的邀请，撤销滥用邀请 | 无（邮件标签能列出邀请邮件，但只能重试 reset/verify，`admin_routes.py:145-152`） | 无 | 缺后端 | 跨空间的元数据列表（按需脱敏地址）；带理由和安全日志的撤销；关联投递状态 | 目标 `platform.workspaces.manage` | 全局 | 中 | 复用 `Invites.revoke` + invitation_revoked 日志 | 被撤销邀请的链接在预览和接受时都失败；审计显示平台操作者 | 未实现 |
| A48 空间 | 成员导入 | 平台监管：跨组织列表、停滞的导入、runner 健康、强制取消 | 无（runner 是否存活不在探针里，`ac/app.py:194` 只注入了邮件 worker） | 无 | 缺后端 | 列表与详情（计数、状态、耗时、错误，不含文件内容）；runner 存活与积压；强制取消的语义与 request_cancel 相同 | 目标 `platform.orgs.manage` | 全局 | 低 | member_imports、member_import_rows；MemberImportRunner.alive() | runner 停止时运行中的导入显示为停滞；强制取消只跳过待处理的行，不声称撤回已发出的邀请 | 未实现 |
| A49 空间 | 有效访问 | 解释某用户在某空间、某应用上为何被允许或拒绝 | 零件：`core/apps.py:173-227`、`:45-75`、`core/http.py:372-396`、`core/authority.py:118-158` | 无 | 缺后端 | 逐道闸门说明：账户状态、成员与角色、空间状态、组织状态与成员、空间应用、资格、访问策略、平台应用状态；解释时不以该用户身份行事 | 目标 `platform.roles.view` | 对象 | 低 | 只读 | 解释与该用户实际打开 Docs 的结果一致（同一代码路径），且解释本身不授予访问 | 未实现 |
| A50 空间 | 空间级自定义角色 | 创建、分配 | 无（`docs/platform/organization-roles.md:77`：保留四档固定角色） | 无 | 不适用 | 产品决策；有新需求时再议 | — | 空间 | 低 | — | memberships.role CHECK（`store.py:144-152`） | — |
| A51 配额 | 组织/空间配额（成员、空间、存储、项目、任务）与用量 | 设置并执行；查看用量 | 无：`Organizations.get` 故意返回 `"quota": None`（`organizations.py:196-203`）；只有计数 | 组织和空间标签页上的计数 | 缺后端 | 配额模型，以及真实的执行点（接受邀请、建空间、Docs 上传与任务准入）和用量来源；没有计费，不编造商业档位 | 目标 `platform.config.manage` | 组织/空间 | 中 | 由各执行点读取 | 超额时服务端以明确的码拒绝；显示的用量与实际行数、字节数一致 | 未实现 |

### 2.3 应用与业务

#### 应用目录与开放范围

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B1 应用目录 | 应用（Docs、Message） | 全局启停、按空间启停、按成员授予或撤销 | `ac/cli.py:177-254`（`app list/enable/disable/grant/revoke`，审计 `cli.app.*`）→ `core/apps.py:119-173` | 无（CLI 专用，`cli.py:180`） | 仅命令行·有缺陷 | D6、D7；缺影响预览（活跃用户、运行中任务、已发出的链接）；需说明停用不删数据 | 主机；目标 `platform.apps.manage` | 全局/空间/成员 | 高 | apps、workspace_apps、app_entitlements，由以下位置读取：<br>• `core/apps.py:173-227`<br>• OIDC 授权 `ac/provider_routes.py:117,248`<br>• `core/authority.py:161-167`<br>• `core/machine.py:169-181` | 停用后：<br>• 成员下一次 Docs 请求 403 app_access_revoked<br>• 门户卡片消失<br>• 排队任务报 app_not_enabled_for_workspace<br>• 机器调用 403 app_disabled<br>• 重新启用不会复活已撤销的令牌<br>• 停用 messages 后旧 `/api/auth/messaging` 仍返回 410 | 已上线 |
| B2 应用目录 | 应用访问策略（`apps.access_policy` 的 members / explicit） | 修改 | 没有写入方（`apps.py:86-117` 保留原值；CLI 没有对应参数，`cli.py:580-585`） | 无（成员页只读显示，`ac/members.py:208`） | 缺后端 | 受控修改：预览将失去或获得访问的成员；审计；收紧时写安全日志 | 目标 `platform.apps.manage` | 应用 | 高 | `apps.py:221`、`authority.py:237-238` | 改为 explicit 后，没有授予行的成员下一请求返回 403；改回时不清除显式撤销 | 未实现 |
| B3 应用目录 | Message 按组织开放 | 启用、停用 | 无（`apps.py:188` 对所有有效账户开放） | 无 | 缺后端 | 组织级策略，在 `Messaging._member/_org_member`、建群、开私聊处检查；说明数据保留、消息流和桥接卡片在停用后的行为 | 目标 `platform.apps.manage` | 组织 | 中 | 将由 `core/messaging.py` 的守卫读取 | 对组织 A 停用：A 的成员在 A 的会话中发送或列表时得到稳定拒绝码，组织 B 不受影响；重新启用后历史不变 | 未实现 |

#### Docs 对象

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B4 Docs 对象 | 项目 | 跨空间元数据列表（不含名称）；按来源证据批量清理测试项目 | 租户内 `dw/services.py:120-180`；CLI `dw/cli.py:356-446`（`projects list/prune`：打印名称；默认 dry-run；只自动选中 origin `test:*`；直接 store.update；无审计）；后台只有计数 | 空间标签的项目计数 | 仅命令行 | 平台元数据接口：ID、空间、来源、时间、归档/删除、模板版本、任务/来源/产物计数。项目名属于内容，需要独立权限（见 `admin_routes.py:280` 的注释）。批量清理需要服务端快照、逐项结果和逐项审计 | 主机；目标 `platform.docs.view` | 全局/空间 | 中 | projects、jobs、source_files、artifacts，经 Store | 在两个测试空间建项目：列表计数正确且不含名称；普通用户和组织 owner 返回 404；prune 只归档 origin=test:* 的项目，每项一条审计 | 已上线·本地有改动 |
| B5 Docs 对象 | 项目 | 归档/取消归档、软删除/恢复 | `dw/services.py:221-264,302-304`；`dw/api/app.py:402,406`；机器 API `dw/api/v1.py:333`（admin scope）；删除的效果见 `dw/jobs.py:1493-1495`、`dw/authz.py:29-75` | 无（成员在 Docs 项目列表里操作） | 仅命令行（平台侧） | 没有 undelete 服务；删除和归档都不写 audit_events；没有影响预览（将停止的任务、待决的复制、仍存续的组织模板发布）；CLI 不能取消归档 | 租户：`dw/authz.py:288` route_policy；机器 admin scope；主机 | 空间/对象 | 高 | projects.archived_at、deleted，由 App.get_project、JobService、authz._REACHABLE 读取 | 删除一个有运行任务的测试项目：任务被取消并记 halt project_deleted；`GET /api/jobs/{jid}` 返回 404；来源字节返回 404。恢复功能落地后全部重新可达，并有审计 | 已上线·本地有改动 |
| B6 Docs 对象 | 来源文件、模板、产物 | 查看元数据（格式、版本、扫描状态、使用情况、sha256、校验结果）；下架来源；模板软删除与恢复 | `dw/services.py:647-686`（remove_source）、`:826-897`、`:1174`、`:1235-1252`（delete_template，使用中返回 409）；`dw/jobs.py:2015-2025` | 无（成员在 Docs 内操作） | 缺后端 | 平台元数据读取（默认不含字节和文件名）；下架或删除需要内容或生命周期权限、理由和审计；没有取消下架和模板恢复服务；删除会关闭协同房间，需要预览 | 租户：editor / write、admin scope；目标 `platform.docs.manage` | 对象 | 中 | source_files、templates、template_versions、artifacts | 删除未使用的模板：GET 返回 404，房间以 4410 关闭；下架来源后新任务不再包含它，已有任务的快照不变；元数据与 v1 接口一致，sha256 等于 blob 摘要 | 已上线·本地有改动 |
| B7 Docs 对象 | 模板版本 | 还原、撤销、重做（会丢弃实时编辑） | `dw/services.py:1110-1216`（base_head 冲突 409 stale_template、`_guard_live_edits`）；`dw/collab.py:740-766` | Docs 模板编辑器（仅模板编辑者） | 缺后端（平台侧） | 平台路径会修改内容：需要内容权限，显示影响（脏草稿、在线的写者、最后写入者），要求理由并审计；还原后作为新版本保存 | 租户：editor；目标 `content.support_access` | 对象 | 高 | template_versions 的 head + 房间 apply_head_change | 房间有未保存编辑时，不带 discard_live 返回 409 live_edits；带上后房间拿到新 head，变更日志记录还原；旧的离线标签以 4409 关闭 | 已上线·本地有改动 |
| B8 Docs 对象 | 文档、产物、来源的内容 | 出于支持目的查看或导出 | `dw/api/v1.py:967`（export scope）；`dw/api/app.py:865`；`dw/authz.py:288` blob_readable | 无 | 缺后端 | 受控的支持授权：限定对象、用途、期限和可执行动作；保留真实管理员身份；可撤销、可审计。平台管理员按设计没有任何 Docs 路由访问权 | 目标 `content.support_access`（默认不授予任何内置角色） | 对象 | 高 | Docs 会话闸门 + authz.blob_readable | 没有授权时内容路由返回 404；有授权时只能下载该对象，直到到期；撤销后立即停止；审计列出授权和每次读取 | 未实现 |
| B9 Docs 对象 | 签名下载链接 | 列出、撤销 | `dw/api/v1.py:975-1030`（`auth.sign_link` 无状态；TTL 在导入时读取，`:52`；兑现时复查签发者权限） | 无 | 缺后端 | 没有签发记录，不能逐条撤销；只有签发凭据或导出权被撤销、或空间被冻结时才失效；签发不写审计 | 机器 export scope | 对象 | 中 | `core/auth.py` sign_link/open_link + `authority.check(need='export')` | 冻结测试空间后旧链接返回 403 link_revoked；逐条撤销落地后，被撤的链接返回 403，同批的其他链接仍可用 | 已上线·本地有改动 |
| B10 Docs 对象 | 项目评论 | 平台级审核删除 | `dw/comments.py:120-126`（作者或空间 admin）；`dw/api/app.py:244-249`（审计 docs.comment.delete） | Docs 项目评论（空间 admin） | 缺后端（平台侧） | 平台审核需要内容权限和 Docs 举报流，两者都不存在；空间 admin 已经可以审核 | 租户：作者或空间 admin | 对象 | 中 | project_comments.deleted_at | 有权管理员删除被举报的测试评论后，评论连同回复消失，审计记录管理员和对应举报 | 已上线 |

#### Docs 任务与引擎

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B11 Docs 任务 | 任务 | 跨空间列表与筛选；详情（items、runs、events）；按错误码、阶段、格式聚合失败 | CLI `dw/cli.py:339-352`（`jobs list`：最近 20 条，跨全部租户，无筛选）；租户内 `dw/jobs.py:1266-1354`；`dw/api/app.py:798-834`；`dw/api/v1.py:878-927` | 无 | 仅命令行 | 服务端筛选（状态、阶段、halt_reason、空间、来源、时长）与分页；卡住指示（lease_until<now、最老的排队）；下钻到空间、项目、审计。issue 信息含样本值（`dw/engine/templates.py:1082`、`dw/engine/plan.py:152`），没有内容权限时只显示码、阶段和字段名 | 主机；目标 `platform.docs.view` | 全局/空间 | 中 | jobs、job_items、job_runs、job_events | 在两个测试空间播种各种状态的任务：筛选结果与 SQL 一致；失败任务详情与成员视图的码和阶段相同，但不含字段值；非管理员返回 404 | 已上线·本地有改动 |
| B12 Docs 任务 | 任务 | 取消（单个、批量、整个空间） | `dw/jobs.py:1376-1399`；`dw/api/app.py:815`；`dw/api/v1.py:929` | 无 | 缺后端（平台侧） | 平台接口需要理由和审计（现有所有取消路径都不写审计，job.cancel_requested 也不记操作者）；批量需要服务端快照 | 租户：editor / write；目标 `platform.docs.execute` | 对象/空间 | 中 | JobService runner 在条目之间读取 cancel_requested；job.cancelled 事件唤醒 webhook | 取消运行中的测试任务：状态经 cancelling 变为 cancelled；进行中的条目保留结果，不再开始新条目；发出 job.cancelled 事件和 webhook；审计记录管理员 | 已上线·本地有改动 |
| B13 Docs 任务 | 任务 | 重试失败项（failed_only / include_skipped） | `dw/jobs.py:1401-1464`（幂等键、`_admit` 配额、固定快照）；`dw/api/app.py:819`；`dw/api/v1.py:934` | 无 | 缺后端（需先设计） | 两处需先定设计：<br>• 重试以重试者的权限运行（`dw/jobs.py:1497-1499`），非成员的平台管理员发起的重试会被 halt。<br>• operator 主体对一切放行，包括冻结空间（`core/authority.py:136-137`），不得用于管理重试。<br>需要决定：沿用原提交者的权限，还是要求由空间内的行为人发起；幂等键绑定管理员；写审计 | 租户：editor / write | 对象 | 中 | JobService runner + 任务快照 | 对一个 3 项失败、7 项成功的任务用 failed_only 重试：只重跑 3 项（run_no+1），成功产物的 blob 不变，同一幂等键重放不重复；在冻结空间中以 workspace_frozen 被 halt | 已上线·本地有改动 |
| B14 Docs 任务 | 卡住的任务 | 查看、恢复（租约过期或 worker 死亡） | `dw/jobs.py:744-761` recover（启动时和 `_supervise` 每 ≤5 s 执行，`:656-680`）；CLI `jobs recover`（无审计） | 无 | 仅命令行 | 卡住任务视图（租约已过期、排队时长、规划无进展）；带审计的受控恢复 | 主机；目标 `platform.docs.execute` | 全局 | 低 | JobService.recover | 任务进行中杀掉 Docs 进程：租约过期或重启后任务回到 queued，有 job.recovered 事件，最终完成且没有重复产物 | 已上线·本地有改动 |
| B15 Docs 任务 | 队列与 runner | runner/supervisor 健康与积压；日常维护的结果和手动触发；按空间的用量 | `dw/jobs.py:625-652`、`:1569`、`:682-706` maintain（每 600 s 执行，结果不保存）、`:1356-1374` usage；`/readyz` checks.worker；`dw/api/v1_governance.py:71-92`（只能看本空间凭据） | 无（`/readyz` 匿名且只有组件状态） | 缺界面 | 需登录的面板：runner 存活、心跳年龄、占用与上限、按空间的 queued/running、最老排队、最近一次 maintain 结果；按空间用量与消耗最多者；手动 maintain 并写审计 | 目标 `platform.docs.view`、`platform.docs.execute` | 全局（每进程）/空间 | 低 | 进程内的 JobService 状态 | 杀掉 runner 线程：面板显示停止，supervisor 将其重启（`dw/jobs.py:670-672`）；面板用量等于用该空间测试 key 调用 `/api/v1/usage` 的结果 | 已上线·本地有改动 |
| B16 Docs 任务 | 队列 | 暂停受理、排空 worker、恢复 | 无（`JobService.stop` 只用于关停，`dw/jobs.py:644-650`） | 无 | 缺后端 | 持久化的暂停标志，由 `_claim/_admit` 读取；排空时完成当前切片并释放租约；行为与维护模式一致（见 R14） | 目标 `platform.ops.execute` | 全局/空间 | 高 | 将改 `_claim/_admit` | 暂停期间：运行中的条目照常完成，不领取新条目；新提交按规格被拒或保持排队。恢复后队列排空 | 未实现 |
| B17 Docs 任务 | 按组织或空间覆盖 Docs 配额（最大活动任务数、每任务记录数） | 设置 | 无：全局 `max_active_jobs()`（`dw/jobs.py:1110-1122`）、`dw/engine/limits.py:56` | 无 | 缺后端 | 全局/组织/空间三级的配额存储，支持继承和版本检查；在 `_admit` 和建任务处读取 | 目标 `platform.config.manage` | 全局/组织/空间 | 中 | 将改 JobService._admit/create | 把空间 A 限为 2：第三个并发提交返回 429 queue_full limit=2；空间 B 仍可用到全局上限 | 未实现 |
| B18 Docs 引擎 | 能力矩阵、探测缓存、就绪、字体 | 查看；刷新探测；字体报告与缺字体模板报告 | `dw/capabilities.py:27,45,63-107`（`clear_probe_cache` 无调用方）；`dw/engine/fonts.py:88-149`；`/api/v1/capabilities` | Docs 的 `/capabilities` 页（成员可见）；Synwevo Admin 中没有 | 缺界面 | 管理视图：标出降级的行、探测年龄、扫描策略、许可状态，按格式下钻到失败任务；刷新探测；字体报告（装字体是镜像变更，不是界面设置）；LibreOffice 与 OCR 的运行健康不在 readyz 中 | 成员 / read scope；目标 `platform.docs.view` | 全局（每进程） | 低 | docs 进程内的 capability_report | 行内容与 `/api/v1/capabilities` 一致；没有 LibreOffice 的测试实例上 DOC/ODT 显示为 unavailable；装上 OCR 语言后刷新立即出现 | 已上线·本地有改动 |
| B19 Docs 引擎 | 引擎并发、输出版本、回归语料、溢出/分页默认值 | 调整参数；记录每个任务用的引擎版本；运行漂移检测；设置默认值或允许集合 | `dw/services.py:82`（解析池固定 2 个工人）；`dw/engine/converters.py:30,272`（每进程一次只跑一个 LibreOffice）；`dw/jobs.py:297-376`（不记引擎版本）；`dws/regression.py:1-12`；`dw/services.py:283-300` | 无（成员在 Docs `/regression` 管理本空间的用例） | 缺后端 / 回归为仅命令行 | 可配置的解析工人数；队列深度、等待时间、超时计数；在快照或产物上记录构建号、PDF 引擎、soffice 版本；后台回归只跑仓库内的合成用例（空间用例属于内容）；作用域默认值不得改写已有项目的语义 | 主机；目标 `platform.docs.execute`、`platform.config.manage` | 全局/组织/空间 | 中 | docs 进程的线程池与锁；synwevo_docs.regression | 并发上传时指标显示队列深度；新任务详情中的引擎版本等于运行时 `/api/v1/capabilities`；引擎不变时回归以 0 退出 | 已上线（CLI）/ 未实现 |

#### Docs 实时协同

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B20 Docs 协同 | 表格实时协同开关 `SYNWEVO_SHEET_LIVE` | 开启、关闭（带回滚预检）、分批放开 | `dw/collab.py:132-135`（开房间时读取）；关闭会丢失未保存的实时编辑，目前只有主机上的 SQL 预检（`docs/platform/docs-sheet-live.md:234-245`） | 无 | 仅命令行 | 列出脏表格草稿的预检；受控切换 + 重启；审计；目前只能全局开关，不能按组织或空间 | 主机；目标 `platform.config.manage` | 全局 | 高 | CollabHub._open_room 读取环境变量 | 关闭前预检显示 0 个脏草稿；重启后表格房间 hello.live=false，已保存版本完好 | 仅本地（aadcb9e 之后） |
| B21 Docs 协同 | 协同服务与房间 | 健康指标（房间、连接、持久化延迟、写失败、4503、表格操作被拒）；房间元数据列表；协议与客户端版本 | `dw/collab.py:445-485,573-599`（房间在进程内字典里）、`:740-766`、`:1167-1273`（没有版本协商）；readyz 不检查协同 | 无 | 缺后端 | 指标与房间列表：类型、模板/项目、空间、连接数与只读连接数、live、是否脏、最后持久化时间；不含摘录和 awareness 内容。hello 中带协议版本，并提供过期客户端视图。说明这些数据按进程统计（多 worker 需要粘性路由） | 目标 `platform.docs.view` | 全局（每进程） | 低 | CollabHub | 同一模板开两个测试标签：显示 1 个房间、2 个连接；强制草稿写失败后错误计数增加；响应中没有摘录文本 | 未实现 |
| B22 Docs 协同 | 协同连接 | 定向断开 | 只能间接断开：`dw/collab.py:1334-1369` 的 `_recheck` 每 30 s 检查一次，关闭时用 4401/4403；由账户停用、撤销会话、空间冻结触发 | 间接（Users、Workspaces 标签，最长 30 s） | 缺后端 | 按房间或按连接立即断开，要求理由并写审计，不显示内容 | 现为 PA!（间接）；目标 `platform.docs.execute` | 对象 | 中 | CollabHub 关闭连接 | 断开测试连接：客户端收到关闭码；原因存续期间重连被拒；写一条审计 | 已上线（间接） |
| B23 Docs 协同 | 协同草稿 | 列出脏草稿；丢弃或保存（维护） | `core/docs_collab.py`；`dw/collab.py:916-930` delete_draft；`dw/services.py:1183` restore_version(discard_live)；主机上的原始 SQL | 无 | 仅命令行 | 用服务级动作取代原始 SQL：列出草稿、通知编辑者、备份后丢弃、重认证、审计；必须经 write_state 守卫与打开的房间协调 | 主机；目标 `platform.docs.execute` | 对象 | 高 | collab_docs + 房间状态 | 丢弃后房间从 head 重新载入，dirty=0，在线标签收到通知，其余不变 | 已上线·本地有改动 |

#### 复制审批与组织模板

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B24 Docs 复制 | 项目复制请求 | 平台监管：跨组织按状态列出（含 stale 和 failed 的原因）；撤销已批准但未执行的请求；对失败请求做幂等重试 | `core/project_copies.py:147-266`（failed 为终态；只有请求者能撤回）；`dw/copies.py:98-138,240-279`；TTL 硬编码 7 天（`project_copies.py:47-48`） | 无（组织内审批在 `acweb/pages/org/OrganizationApprovals.tsx`） | 缺后端 | 平台视图只含清单元数据（名称、大小、哈希）；允许审批方在执行前撤销；重试绑定原批准，不产生第二个目标项目。平台管理员按设计不是审批人 | 目标 `platform.orgs.view`、`platform.orgs.manage` | 全局/组织 | 中 | project_copies 状态机 + Docs CopyExecutor | 失败后重试只产生一个目标项目；摘要不符时进入 stale，不再重试；审计串起请求、决定和结果 | 已上线·本地有改动 |
| B25 Docs 组织模板 | 组织模板发布 | 平台元数据视图；平台下架（滥用或法律原因） | `dw/org_templates.py:50-183`（维护者可以 deprecate/restore，写审计）；`dw/rescan.py:432-441` 重扫时拒收被感染的发布 | Docs 组织模板面板（维护者） | 缺后端（平台侧） | 平台只读视图（组织、来源空间、release_no、格式、使用次数、是否弃用、扫描状态）；带理由的平台下架。已有副本按设计不受影响 | 租户：来源空间 editor 或组织 owner/admin | 组织 | 中 | organization_templates.deprecated_at | 下架后选择器中不再显示，"使用"被拒；已有副本仍能生成；有审计 | 已上线 |

#### Message 与沟通治理

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B26 Message | 服务健康与进程计数器 | 查看 | `msg/app.py:54,110`；`msg/routes.py:75` Metrics（send_failed、流的重放/重同步/拒绝、连接数、ring、presence） | 无（只有消息源站公开的 `/readyz`） | 缺界面 | 管理接口读取消息进程的 `/readyz`（计数在内存里，账户进程读不到数据库）；概览卡片附定义和采样时间，进程不可达时显示"不可用"；计数随重启归零 | PA | 全局 | 低 | 单个消息 worker 进程中的 Metrics/MessageHub | 测试用户开一条流并发一条超长消息：卡片上的 stream_connections 和 send_failed 与消息源站 `/readyz` 同时变化 | 已上线 |
| B27 Message | 会话与群的元数据（不含正文） | 列表、搜索、查看 | 没有管理接口；数据模型见 `core/messaging.py:66,80`；只能按本人列出（`:1013`） | 无 | 缺后端 | 字段：组织、类型、群名（私聊不显示对方姓名）、成员数、拥有者、创建/归档时间、最后活动、开放的举报数。服务端筛选和分页；不返回正文和附件名；搜索写审计 | 目标 `platform.messages.view` | 组织 | 中 | 只读 SQL | 管理列表显示测试群的计数，不含消息文本；无权者返回 404/403；搜索写入审计 | 未实现 |
| B28 Message | 群的生命周期、所有权与成员（版主操作） | 归档/恢复、强制转让、设管理员、移除单个成员 | 目前只有群主或群管理员能做：`core/messaging.py:325,791,822,834,844,894`；`msg/routes.py:428`；组织移除成员时调用 `:885` | 无（群主在 Message 信息面板中操作） | 缺后端 | `org.messaging.manage` 的描述写着"可归档群"（`core/org_roles.py:86-88`），但服务不认这个权限，标签夸大。需要组织级或平台级的版主路径：理由、重认证、系统消息、审计、通知成员、处理桥接卡片；保持"群永远有主" | 现为群主；目标：租户 `org.messaging.manage`、平台 `platform.messages.manage` | 对象 | 中 | conversations.archived_at、conversation_members；每次读取和流事件都重读（`msg/routes.py:803`） | 版主归档后：成员发送返回 409 archived，历史仍可读，系统消息署名版主。移除成员后：其历史、搜索、附件、流全部 404 或结束 | 已上线（仅群主路径） |
| B29 Message | 消息举报（组织） | 列表；复核（移除或驳回） | `ac/messaging_reports_routes.py:57,64` → `core/messaging.py:694-721` | 租户界面 `acweb/pages/org/OrganizationReports.tsx`（`Organizations.tsx:90`） | 已接入（租户）·有缺陷 | D13；列表上限 200 条，没有分页 | 租户：org.messaging.manage + 严格重认证 | 组织 | 中 | message_reports；移除时清空 body、设 deleted_at、删除反应，FTS 触发器清索引 | A 举报一条消息，管理者复核选"移除"：重同步后显示为已删除，搜索不到，举报人收到 report_removed，有审计 messaging.report.review；并发的第二次复核必须返回 409 | 已上线 |
| B30 Message | 消息举报（平台）与申诉 | 跨组织队列、升级、证据访问；申诉与复核记录 | 无；举报 status 为终态（`core/messaging.py:76,708`） | 无 | 缺后端 | 平台队列：按组织和时长计数；组织内没有有效处理人时升级。查看冻结的 body_snapshot 需要独立、限定用途的内容权限，平台管理员默认看不到。申诉和复核有历史，不静默恢复已移除的内容 | 目标 `platform.messages.manage` + `content.support_access` | 全局/组织 | 高 | message_reports | 没有内容权限时只能看到计数；有权限并写明用途时，只能看到该条举报的快照，两种访问都写审计；申诉后维持或推翻原决定，历史中两条都在 | 未实现 |
| B31 Message | 用户发送限制 | 在组织内或全平台禁言（带到期时间） | 无；只有账户停用和从组织移除两种手段 | 无 | 缺后端 | 限时限制，在 send、attach、forward、create_group 处检查；带理由、到期时间、审计和申诉；不能对受限用户假装"已发送" | 目标 `platform.messages.manage` | 组织 | 中 | 将由 `core/messaging.py:1231` 读取 | 受限用户在该组织发送或上传时得到稳定的拒绝码，仍可阅读；到期后自动解除 | 未实现 |
| B32 Message | 用户的消息流 | 强制断开 | 只能间接断开：撤销会话或停用账户；流每 25 s 重检一次（`msg/routes.py:773,816`） | Users 标签 | 已接入（间接） | 不能只断开这一个应用；最长延迟 25 s，UI 没有说明；看不到用户开了几条流 | PA! | 全局 | 中 | 消息进程的心跳重读 sessions 和 users | 测试用户开流后撤销其会话：25 s 内收到 end，重连被拒 | 已上线 |
| B33 Message | 历史可见性（history_from 为 join 或 all）；消息保留 | 设置组织策略并预览影响；设置保留期 | 可见性只能在每次加人时选择（`core/messaging.py:746`、`msg/routes.py:378-393`）；没有任何保留或过期代码 | 无 | 可见性：缺后端；保留：不适用 | 可见性：组织策略可禁止 all（或只允许非私密群用 all），不追溯扩大已有成员的可见范围。保留：产品没有保留策略，这是产品或法务的决定，后台不放假开关 | 目标 `platform.messages.manage` | 组织 | 中 | conversation_members.joined_seq | 策略为"仅加入后"时 history_from=all 被拒，已有成员的 joined_seq 不变；覆盖表写明"无保留策略" | 未实现 |
| B34 Message | 全文索引（messages_fts） | 查看状态、重建 | `core/messaging_search.py:90` rebuild（无调用方，也没有 CLI）、`:107` describe | 无 | 缺界面 | 管理状态（引擎、分词器、行数、回退情况）；在消息进程中加锁执行的重建作业 | 目标 `platform.ops.execute` | 全局 | 中 | SQLite FTS5 | 状态显示 fts5/trigram；重建后同一个 3 字查询返回相同的受限命中 | 已上线（未接线） |
| B35 Message | Docs→Message 桥接 outbox 与 worker | 查看待处理、失败、死信；重投；worker 健康；立即对账 | `core/messaging_bridge.py:55,437,526-611`（最多重试 10 次，每 60 s 扫描，用行 id 作 client_id 保证幂等）；不在 `/readyz` 里 | 无 | 缺后端 | D18：列表（按类型和状态计数、时长、错误码、次数，不含载荷标题）；保持幂等的重投（理由 + 审计）；worker 存活和最后一次扫描时间；立即对账（单个会话或全部） | 目标 `platform.ops.view`、`platform.ops.execute` | 全局 | 中 | 消息进程的 BridgeWorker 线程 | 强制造一条失败行：显示失败状态和次数，10 次后显示为死信；重投死信只出现一张卡片，再投一次也不会出现第二张 | 已上线（无入口） |

#### 通知、邮件与推送

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B36 邮件 | 发件箱（mail_outbox） | 列表；按状态和地址筛选 | `GET /api/admin/mail`（`admin_routes.py:154-168`：行已脱敏，只给安全错误码和 delivery_mode） | 邮件标签（`GovernanceTabs.tsx:42-75`） | 已接入 | 不能按类型或日期筛选；没有导出；通知投递与邮件之间没有链接；"SMTP 已接收"的标注是正确的，最终是否送达以外部为准 | PA | 全局 | 低 | 账户进程的 Mailer worker（`ac/mail.py:229`） | 在 file 模式下为测试账户入队一封验证邮件：状态从 queued 变为 sent，error_code 为空；强制 SMTP 认证失败时显示 smtp_authentication_failed | 已上线 |
| B37 邮件 | 失败的重置和验证邮件 | 生成新令牌并重试 | `POST /api/admin/mail/{mid}/retry`（`admin_routes.py:201-218`：只接受已知的安全错误码；旧行标为 superseded） | 邮件标签"生成新链接并重试" | 已接入 | 其他类型的邮件按设计不能重试（`:147`），UI 应说明原因；没有批量 | PA! | 对象 | 中 | 新的 email_tokens 行 + 新的 mail_outbox 行 | `test_admin_governance.py:124-167,239-257`：旧行变为 superseded，新行已发送，旧令牌失效，审计 admin.mail.retry 关联新旧两个 ID | 已上线 |
| B38 邮件 | 其他类型的邮件（邀请、通知、登录码、账户已存在、安全、换邮箱、删除） | 重发、取消排队、标注 delivery_unknown | 无；Mailer 没有取消；delivery_unknown 是终态（`ac/mail.py:44-47,231-238`） | 无 | 缺后端 | 按类型定规则：<br>• 邀请走邀请服务（新令牌、去重）。<br>• 通知重新走 stale_reason 和偏好检查。<br>• 登录码和过期令牌永不重发。<br>• 只有未被 worker 领取的邮件可以取消。<br>• delivery_unknown 只能人工标注。 | 目标 `platform.mail.manage` | 对象 | 中 | Mailer、邀请服务、NotificationDispatcher | worker 领取前取消一封测试邮件：状态变为 cancelled，文件出口中没有内容；重发登录码被拒 | 未实现 |
| B39 邮件 | 发信配置（MAIL_MODE、SMTP_*、MAIL_FROM、MAIL_FROM_NAME、MAIL_DIR、PUBLIC_URL） | 查看脱敏状态；修改和轮换凭据；连接测试；向授权地址发测试信 | `ac/mail.py:62,91-152`（只在发送时校验，配置无效返回 503 mail_config_invalid）；smtp.env 只提供给账户容器（`prod/compose.yaml:50-56`）；`services/identity/scripts/verify_smtp.py`（用新建的隔离数据目录，最多发 2 封） | 邮件标签只显示投递模式 | 查看：缺界面；修改：仅主机；连接测试：缺后端；测试发信：仅命令行 | 需要补齐：<br>• 脱敏视图：主机、端口、TLS 方式、超时、发件人，用户名和口令只显示"是否已设"（不显示值，也不显示长度）；校验结果；来源；是否需要重启。<br>• 有界探测：连接、校验证书的 TLS、AUTH、NOOP/QUIT，只返回安全码；目标主机固定，限流，审计。<br>• 测试信只能发往允许的测试地址。<br>• 轮换仍是运维动作，后台显示漂移。 | 现为 PA / 主机；目标 `platform.mail.manage` | 全局（账户角色） | 高 | 账户进程的环境变量，每次调用 mode()、smtp_config() 时读取 | 页面显示的非秘密字段与容器环境一致，响应中没有口令；用错误口令探测返回 smtp_authentication_failed 且不产生邮件；测试信生成一行 kind=test 且状态为 sent | 已上线（值待核实） |
| B40 邮件 | 发信域认证、退信与投诉、发信总量、多发件地址 | 查看 SPF/DKIM/DMARC；退信和投诉入库并维护抑制名单；全局和按类型的限额；多地址路由 | 无（DNS 靠人工检查，见 `docs/operations.md:265-279`；最终投递结果只在外部中继里；只有单一发件人，`ac/mail.py:150`） | 无 | 缺后端 + 缺外部配置；多发件地址：不适用 | 只读 DNS 查询并显示检查时间，不能点一下就标为通过；退信入库并映射到 outbox，`Mailer.enqueue` 遵守抑制名单；发送预算及其可见状态；代码中只有一个 MAIL_FROM，按 §7.8 记为不存在 | 目标 `platform.mail.manage` | 全局 | 中 | DNS 解析（只读）；将改 Mailer.enqueue | 状态反映真实的 TXT 记录并带检查时间；一条测试退信事件把对应行标为退信，并拒绝下一封发往该地址的邮件 | 未实现 |
| B41 邮件 | 邮件与通知模板 | 三语言预览；编辑、版本、变量校验、发布、回滚 | 模板写在代码里：`ac/mail.py:388-555`、`ac/mail_layout.py:106`、`core/notifications.py:99`；预览脚本 `services/identity/scripts/mail_preview.py` | 无 | 预览：仅命令行；编辑：缺后端 | 后台预览：HTML + 纯文本、三种语言、变量契约、用合成数据、不发送。是否把文案移出代码需要决定；如果移出：版本化存储、变量检查、不执行服务端代码、改版后不重发历史事件 | 代码；目标 `platform.mail.manage` | 全局 | 中 | mail_layout.render、Notifier.render | 同一输入下，后台预览与 mail_preview.py 的输出逐字节相同 | 已上线（预览脚本） |
| B42 通知 | 事件目录；投递队列（notification_deliveries、push_queue） | 查看目录；查看积压、失败、跳过及原因；重试失败项 | `core/notifications.py:82,99,338-357,632-648`；`ac/notify.py:74,138` | 无 | 目录：缺界面；队列与重试：缺后端 | 需要补齐：<br>• 只读目录：接收者、渠道默认值、是否关键。<br>• 队列：按状态计数，列出跳过原因，关联 outbox 行。<br>• D18：`ac/notify.py:9` 所说的"管理员 outbox 重驱"不存在。<br>• 重试在发送时重新检查 stale_reason、偏好、免打扰，不重发已读或已静音的条目。 | 目标 `platform.mail.view`、`platform.mail.manage` | 全局/对象 | 中 | 账户进程的 NotificationDispatcher | 关闭渠道后通知显示 skipped；SMTP 故障时显示 pending 和下次重试时间；重试一条已被读取的条目时以"应用内已读"跳过，不产生邮件 | 已上线（无入口） |
| B43 通知 | 摘要与重试预算（DIGEST_WINDOW_S、CONVERSATION_DIGEST_INTERVAL_S 30 分钟、每日上限 8、RETRY_DELAYS_S、INTERVAL_S、BATCH）；默认偏好策略 | 查看、修改 | 代码常量：`core/notifications.py:88-93,236,289,492,589`；`ac/notify.py:34-35` | 无 | 缺后端 | §6 点名的管理值：类型化配置，带上下界，发送时读取。默认策略只作用于没有保存过偏好的读者；以下规则锁定：安全类通知恒开；会话邮件默认关闭，而且永远不能设为逐条发送 | 代码；目标 `platform.config.manage` | 全局 | 中 | NotificationDispatcher | 把间隔设为 60 分钟后，同一读者的第二封会话摘要推迟到上一封之后 60 分钟；把会话默认值设为 immediate 被拒 | 未实现 |
| B44 通知 | 用户的通知偏好 | 只读诊断；代用户修改 | `core/notifications.py:476,492`；本人使用的路由 `authc/session_routes.py:312-317` | 无 | 只读诊断：缺后端；代改：不适用 | 只读诊断视图，写审计，不显示收件箱内容；代用户修改被 §1.1、§4.3 禁止，不做 | 目标 `platform.users.view` | 对象 | 高 | notification_preferences | 诊断结果与该用户自己 GET 偏好的结果一致；测试断言管理员会话不能 PUT 他人的偏好 | 未实现 |
| B45 通知 | 平台公告（站内通知广播） | 撰写、预览受众、发送、撤回 | 无：`platform` 类别存在（`notifications.py:82`），但没有对应事件 | 无 | 缺后端 | 目录项；受众预览；内容版本；经 Notifier.emit 发送（遵守偏好和免打扰）；幂等批次；撤回（已发出的邮件撤不回）。绝不用于私信邮件 | 目标 `platform.mail.manage`（高风险外发） | 全局 | 高 | Notifier.emit + NotificationDispatcher | 预览显示 N 人；发送产生 N 条，重试不再增加；关闭了平台邮件的用户收不到邮件；撤回后从收件箱消失 | 未实现 |
| B46 推送 | Web Push（SYNWEVO_PUSH、PUSH_CONTACT）；VAPID 密钥 | 查看状态、开关；查看指纹、轮换 | `core/push.py:70-104`（密钥在首次使用时生成并入库；schema 有 retired 状态，但没有轮换代码）；`ac/push_routes.py:34` | 无 | 开关：仅主机；轮换：缺后端 | 只读状态：是否启用、是否有联系人、密钥 id 和公钥指纹。轮换需要：退役旧密钥、触发重新订阅、影响预览、审计 | 主机；目标 `platform.config.manage` | 全局 | 高 | push_keys；WebPush.headers | SYNWEVO_PUSH=off 并重启后，`/api/auth/push/config` 返回 enabled=false，且不再产生 push_queue 行；轮换后返回新公钥，设备重新订阅 | 已上线（值待核实） |

#### API、服务身份与集成

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| B47 API 平台 | API 管理控制台 | 进入；概览（30 天调用量、待审批、资源、scope） | `ac/api_platform.py:36-51,102`；`core/api_platform.py:59` | `/admin/api-platform`（`acweb/pages/ApiPlatform.tsx`）；另有入口链接 | 已接入 | 没有纳入 `/admin` 的信息架构；不能选时间范围，不能按路由或空间分解；独立部署的账户服务没有适配器时返回 503 | APM；被拒绝的请求会写审计 | 全局 | 低 | 账户进程读 api_platform_calls 和 approvals | `it/test_api_platform.py::test_only_platform_admin_has_management_permission`、`::test_workspace_roles_cannot_use_console_or_approve`、`::test_signed_out_and_live_admin_demotion`；`it/api_platform_browser_acceptance.py` | 已上线 |
| B48 API 平台 | API 审批（空间所有者同意） | 申请；列表；所有者批准或拒绝（scope 取交集）；撤销；所有者变更等情况下自动失效 | `ac/api_platform.py:119-171`；`core/api_platform.py:103-169`；组织撤回时撤销审批（`core/organizations.py:476`、`core/journal.py:208`） | 控制台审批页（`acweb/pages/apiplatform/Approvals.tsx`）；所有者页 `/account/api-approvals` | 已接入 | 空间要手输原始 ID，没有选择器；不检查空间是否有具备 Docs 资格的所有者；列表不能按空间、申请人、类型、到期时间筛选；撤销前没有影响预览；失效时只显示原始错误码 | 申请与撤销：APM!；批准：只有当前空间所有者，且需严格重认证和理由（平台管理员没有批准权） | 空间 | 高 | api_platform_approvals；每次 `/api/v1` 调用都经 `MachineAuth.verify → enforce_principal` 检查 | `test_api_platform.py::test_owner_consent_issue_is_one_use_and_scope_intersection`、`::test_live_approval_authority_checked_at_issue_use_and_deferred_work`、`::test_unconsumed_revocation_survives_restore_journal`、`::test_bound_credentials_listing_reports_lost_approval`；`test_api_platform_races.py` | 已上线 |
| B49 API 平台 | 所有者待办 | 新申请通知、到期前提醒、导航入口 | 无（`Approvals.create` 不写通知；没有任何链接指向 `/account/api-approvals`） | 无 | 缺后端 | 收件箱或邮件事件；到期前只提醒一次；在账户和门户导航中加链接 | 系统自动 | 空间 | 中 | 将使用 Notifier | 提交申请后，所有者收件箱出现条目，深链接能打开审批页 | 未实现 |
| B50 API 平台 | 审批续期 | 延长到期时间并保留原凭据 | 无 | 无 | 缺后端 | 所有者再次同意后延长原绑定，并写审计；今天审批一到期凭据就永久停用 | 所有者同意 | 空间 | 中 | — | 续期后 client_id 或 key 谱系不变，过了原到期时间 `/api/v1` 仍返回 200 | 未实现 |
| B51 API 平台 | 机器凭据（dw_ API key、Docs 服务账户） | 签发（秘密只显示一次）；列表；轮换；停用/撤销；重新启用；撤销全部令牌 | `ac/api_platform.py:178-290`；`core/auth.py:277,383,484`；`core/oidc.py:186,278,314,322,567` | 控制台凭据页（`acweb/pages/apiplatform/Credentials.tsx`） | 已接入·有缺陷 | D4、D16；不显示 grace_until、last_used_at、最后一次调用、取交集后的有效 scope；轮换后旧凭据作为单独一行出现；只列出 Docs 服务账户；停用前没有影响预览 | APM!；在事务内复查 | 对象/空间 | 高 | api_keys、oauth_clients、api_platform_bindings；Docs 网关、job runner、webhook 分发器都遵守 | `test_api_platform.py::test_api_key_rotation_keeps_approval_and_revoke_stops_lineage`、`::test_service_rotation_disable_enable_and_token_revocation`；`test_api_platform_races.py::test_simultaneous_consumption_creates_exactly_one_bound_credential`；补测：在控制台选择过渡期 G 后，旧密钥在 G 内仍可用 | 已上线 |
| B52 API 平台 | 未绑定审批的历史凭据 | 创建、轮换、列表、修改 scope 或空间、并入审批模型 | `dw/cli.py:115-214`（apikey create/rotate/list，可以建永久密钥）；`ac/cli.py:347-397`（client register --service）；自助轮换 `dw/api/v1_governance.py:131` | 控制台把它们显示为"历史凭据"，并拒绝轮换（approval_required） | 仅命令行·有缺陷 | D5；缺少把这类凭据绑定到新审批的迁移流程 | 主机（审计中的操作者是 cli 的 OS 用户或 system） | 空间/对象 | 高 | api_keys、oauth_clients；`core/machine.py:144,247` | 用 CLI 建一个测试 key，控制台将其标为"未绑定"；绑定后撤回审批 scope，调用返回 403 approval_scope_withdrawn | 已上线 |
| B53 API 平台 | 凭据的有效权限 | 不持有秘密就能解释调用为何被拒 | 没有接口；逻辑在 `core/api_platform.py:189`、`core/machine.py:165,247`、`core/apps.py:248` | 无（调试器需要粘贴秘密） | 缺后端 | 按凭据类型和 id 复查审批、所有者、空间状态、应用启用、scope、受众，返回允许/拒绝及原因，不执行业务调用 | 目标 APM | 对象 | 中 | 只读复用 enforce_principal | 冻结空间后，解释返回 workspace_frozen，`/api/v1` 调用返回同样的原因 | 未实现 |
| B54 API 平台 | 用户委托（OAuth 授权族） | 按用户或客户端列出；撤销单个；撤销全部（经撤销会话或停用账户） | 单个：`core/oidc.py:572` revoke_family（目前只在内部使用）；全部：`admin_routes.py:419,466` | 撤销全部在 Users 标签；单个无入口 | 列表：缺后端；撤销单个：缺界面；撤销全部：已接入 | 元数据列表；撤销单个时要理由、重认证，写 tokens_revoked 日志，不影响该用户的其他会话 | 现为 PA!；目标 `platform.users.manage` | 用户/对象 | 中 | oauth_tokens；Docs 网关每次调用都重新推导委托 | 撤销单个委托后，其访问令牌和刷新令牌分别返回 401 和 invalid_grant，该用户其他会话不受影响 | 已上线（撤销全部） |
| B55 API 平台 | OIDC 客户端（portal-web、docweave-web、messages-web、第三方）与签名密钥（JWKS） | 列表和详情；注册、修改回调；启用停用；轮换机密客户端的密钥；撤销令牌；轮换和退役签名密钥 | `ac/cli.py:268-427`（client、keys 子命令）→ `core/oidc.py:140-186,278-322,567`；`/oidc/jwks.json` | 无（控制台只管 Docs 服务账户） | 仅命令行·有缺陷 | 缺只读列表和漂移检查；注册会替换回调列表，但事先没有差异预览；停用第一方客户端会导致所有人无法登录，需要警示；启用不写日志；D9；密钥材料永不显示 | 主机（审计 cli.client.*、cli.keys.*） | 全局 | 高 | oauth_clients、oidc_keys | 依赖方回调往返成功；错误的回调地址在 authorize 阶段被拒；轮换后 JWKS 同时含两个 kid，旧 ID 令牌仍可验证；退役当前密钥被拒 | 已上线 |
| B56 API 平台 | 调用记录、契约与 SDK、调试器 | 查看和筛选调用；浏览契约；执行有边界的只读机器请求 | `ac/api_platform.py:291-332`；调用写入 `core/http.py:1030-1040` → `core/api_platform.py:237`；`dw/control_plane.py:23-115`；SDK 链接硬编码为 GitHub main 分支 | `Observability.tsx`、`Requests.tsx` | 已接入 | 不能按凭据、路由、状态类别、时间筛选，也不能导出；契约版本不显示；SDK 链接指向 main 而不是部署版本；调试器审计只记状态码；D20 | 查看：APM；调试器：APM! + 真实的 api_key 或服务账户凭据 | 全局/空间 | 中 | 每个 `/api/v1` 响应写一行 api_platform_calls；ASGI 进程内调用 | `test_api_platform.py::test_real_gateway_populates_call_log_without_content`、`::test_debugger_refuses_arbitrary_urls_paths_downloads`、`::test_debugger_uses_only_explicit_machine_and_object_scope`；`test_api_platform_races.py::test_asgi_debugger_has_real_coroutine_deadline` | 已上线 |
| B57 Webhook | 空间的 webhook 订阅 | 增删改查、暂停/恢复；签名密钥轮换；ping；查看投递与重投 | `dw/api/v1_webhooks.py:73-145` → `dw/webhooks.py:1200-1389`；控制台代理 `dw/control_plane.py:31-37`（不含 /ping）；CLI `dw/cli_webhooks.py:108-215` | 控制台的 Webhook 标签（需要该空间 docweave.manage 凭据） | 已接入·有缺陷 | 管理员必须持有空间凭据的秘密才能操作；没有跨空间视图；D4；没有 ping 按钮；投递不能按状态筛选；D20；变更不写审计（见 G22） | APM! + 凭据；CLI 为主机 | 空间/对象 | 高 | Docs 进程的 WebhookDispatcher | `test_api_platform.py::test_webhook_real_subscription_rotation_pause_retry_and_scope`；过渡期内请求头带两个签名；ping 私网地址被拒 | 已上线 |
| B58 Webhook | 跨空间订阅健康；部署级 webhook 端点 | 查看失败或自动停用的订阅；配置部署级端点、轮换其密钥 | CLI `webhooks list`；自动停用逻辑 `dw/webhooks.py:817-868`；部署级端点读环境变量（`:141-168`），`deliveries` 没有路由 | 无 | 仅命令行 / 仅主机 | 只含元数据的总览，接入待处理中心；部署级端点的只读状态，秘密永不显示；轮换助手 | 主机 | 全局 | 高 | Docs 进程 | 指向失败接收端的测试订阅，超过 DISABLE_AFTER_S 后显示为 disabled | 已上线（值待核实） |
| B59 API 平台 | 紧急停止 | 冻结单个空间的全部机器访问；全局或按空间停止 Docs 机器 API | 冻结见 A43；`ac/cli.py:194-224`（`app disable docweave`） | 冻结在 Workspaces 标签；全局停止无入口 | 冻结：已接入；全局：仅命令行 | 冻结没有 API 专属的影响预览；全局开关要保证管理员恢复路径和健康检查仍然可用 | PA! / 主机 | 空间/全局 | 高 | workspaces.status；apps 表 | 冻结后返回 403 workspace_frozen；全局停用后返回 403 app_disabled | 已上线 |
| B60 自动化 | 自动化计划 / 定时任务 | 创建、暂停、恢复 | 无（apps/docs 中没有调度实体） | 无 | 不适用 | 产品没有调度模块，建它是新功能，不做假模块 | — | — | 低 | — | grep 只找到内部的扫描重试计时器 | — |

### 2.4 运行与数据

#### 扫描与隔离

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R1 扫描 | 恶意软件扫描器（clamd）与特征库 | 健康、引擎与特征库版本、连接测试；立即运行 freshclam；特征库过期告警 | `core/malware.py:174-219`；各角色 `/readyz` 的 checks.malware_scanner；freshclam 每小时运行一次（`prod/compose.yaml:88-100`） | 无（隐私页只显示开/关/故障，`portal/public.py:308,374`） | 缺界面 / 仅主机 | 按角色的面板，显示特征库日期和检查时间；故障历史；匿名访问仍只看到开/关/故障；freshclam 作为主机上的固定动作 | 目标 `platform.ops.view` | 全局（每角色） | 低 | clamav 容器 | 停掉 clamav：各角色显示 down 并带时间戳；启动后恢复 ok | 已上线·本地有改动 |
| R2 扫描 | 上传闸门的扫描结果（Docs 来源和模板、消息附件、成员导入） | 跨空间的元数据列表和详情；检测记入审计；上传者是否已被通知；对 scan_failed 立即重试 | `dw/services.py:360-467`（重试退避 30–600 s，计时器在内存里）、`:597-608`；`core/messaging.py:1576-1612`；`core/member_import.py:115-150`；检测结果只写 acceptance_log | 无 | 缺后端 | 跨空间查询，默认不含文件名和内容；检测写审计；"立即重试"复用 `_scan_then_parse` | 目标 `platform.files.view`、`platform.files.execute` | 全局/空间 | 中 | 各进程 | 上传 EICAR：列表显示特征，有审计，上传者收件箱有 docs.file_rejected；立即重试不必等退避，且只扫描一次 | 已上线·本地有改动 |
| R3 扫描 | 扫描前就已存储的历史文件（Docs、Message） | 重扫的预览与执行 | `dw/rescan.py:801-902`；`core/messaging_rescan.py:37-282`；CLI `dw/cli.py:474-532`、`msg/cli.py:31-40` | 无 | 仅命令行 | 在 Docs 或消息进程中执行的异步作业（通过组合根适配器）：预览、进度、逐单元结果、取消与续跑、重认证、理由、影响预览；审计记管理员本人 | 主机；目标 `platform.files.execute` | 全局/空间 | 高 | clamd + 扫描记录 + data/quarantine + 通知 | 感染行被拒收并有隔离条目，上传者被通知；干净行标 rescan=true；审计含计数；再跑一次没有新增 | 已上线·本地有改动 |
| R4 扫描 | 重扫中发现的加密历史文件 | 复核，由所有者决定 | 目前只记录，访问不变（`core/rescan.py:26-34,77,120-137`） | 无 | 缺后端 | 列表给所属空间的管理员（平台只看计数）；决定流程与通知；不能直接标为安全 | 租户：空间 admin；平台只看计数 | 空间/对象 | 中 | — | 选"撤回"后不可下载；选"保留"后仍返回 200；有审计 | 仅本地（aadcb9e 之后） |
| R5 隔离 | 隔离索引与一致性 | 列表；检测不一致并执行 settle | `core/quarantine.py:166-173,352,439,478`；`core/rescan.py:402` | 无 | 仅命令行 | 只读管理接口（账户源可以直接读）；健康指示；在 run_lock 下由管理员触发 settle | 主机；目标 `platform.files.view`、`platform.files.execute` | 全局 | 中 | data/quarantine 下的索引 | 后台列表与 `quarantine list` 输出一致；人为造一个不一致后，指示出现，settle 后消失 | 已上线·本地有改动 |
| R6 隔离 | 隔离条目 | 释放（特征撤回后的误报）；清除（永久删除字节） | 释放：`dw/rescan.py:646-790`、`core/messaging_rescan.py:162-235`、`core/quarantine.py:401`；清除：`core/rescan.py:421-455`、`core/quarantine.py:447` | 无 | 仅命令行 | 高风险动作：理由、重认证、按应用预览影响、异步执行、审计记管理员身份；清除还要影响预览和输入确认 | 主机；目标 `platform.files.execute` | 对象（按 sha256，跨空间） | 高 | Docs 进程与消息进程 | 引擎改判干净后释放：行恢复原状态，下载返回 200；引擎仍判感染时释放被拒且没有变化；清除后字节消失，行仍为拒收，同样字节再次上传被拒 | 已上线·本地有改动 |
| R7 隔离 | 被隔离、拒收、加密或未扫描的字节 | 管理员下载、预览、"标记为安全" | 读闸门：`dw/authz.py:91-92`、`core/messaging.py:1615-1634`、`dw/services.py:335` | 无（正确地没有） | 不适用 | §7.10 禁止；补一条回归测试，断言没有任何管理路由能取到这些字节 | — | 对象 | 高 | 各角色的读闸门 | 平台管理员请求这些字节时返回 404 或 410 | — |

#### 存储、保留与数据请求

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R8 存储 | blob 清点；被引用但已丢失的 blob | 查看清点结果；丢失时告警并定位所属对象 | `dws/blob_audit.py:124,305`（只读）；`core/blob_refs.py:17-89`；`dws/snapshot.py:375` | 无 | 仅命令行 | 有时限的只读作业，在 Docs 进程或组合根中运行；默认只给汇总；定时检查并告警（给出对象类型和 ID，不含名称） | 主机；目标 `platform.files.view` | 全局 | 中 | 以只读方式打开 SQLite，并遍历 blobs 目录 | 与在同一副本上运行 `blob_audit.py --json` 的结果一致；删掉一个 blob 后出现告警，恢复后消失 | 已上线·本地有改动（`blob_refs.py` 仅本地） |
| R9 存储 | 未被引用的 blob 回收；内容保留策略（产物、模板版本、软删除的内容、预览授权、回归用例、已删组织、Docs 硬删除） | 预览、移入待定区、恢复、清除、定时执行；定义并应用保留策略 | 无，只有设计（`docs/platform/blob-reclamation.md` §2–6、B2–B6、D1–D7）；`artifacts.deleted_at` 从未被写入（`core/store.py:710`） | 无 | 缺后端（需所有者决定） | 按行的保留策略及其影响预览；回收在 run_lock 下进行，要做引用检查并处理并发；`/readyz` 显示回收状态；从备份中排除；说明部署前的备份仍会保留 | 目标 `platform.files.execute` | 全局/组织/空间 | 高 | 将由 Docs 的 CLI 或 worker 执行 | 按设计 §5 的场景测试；清除后磁盘减少量等于预览量，blob_audit 报告 0 个缺失 | 未实现 |
| R10 存储 | 存储用量与配额；主机磁盘 | 按组织、空间、应用查看；设置配额；容量告警 | 无（不编造存储配额；`/readyz` 只检查存储是否可写，`core/http.py:1199-1206`） | 无 | 缺后端（配额待决定） | 共享 blob 的用量归属（逻辑大小与物理大小）；配额先由所有者决定；数据卷可用 statvfs 读取，备份目录需要在主机侧读取 | 目标 `platform.files.view`、`platform.config.manage` | 组织/空间/全局 | 中 | 将为聚合作业或主机代理 | 在隔离副本上，总量与 blob_audit 的结果一致；填满测试卷后触发告警 | 未实现 |
| R11 存储 | 未发送的消息附件（保留 1 小时） | 定时清理 | `core/messaging.py:1636`，没有调用方（D18） | 无 | 缺后端（缺陷） | 接入消息 worker 的计划任务；字节交给回收流程 | 目标 `platform.ops.execute` | 全局 | 低 | 将由消息 worker 执行 | 上传后未发送的附件 1 小时后被删除 | 已上线（未接线） |
| R12 数据请求 | 个人数据导出（本人发起）；组织或空间数据导出；已删除账户的数据清除 | 查看；创建、批准、交付、过期；宽限期后清除 | 个人导出：`ac/account_routes.py:229-257`（审计 auth.data.export）；组织导出：无；已删账户：`ac/profile.py:125-155` 只做匿名化并移除凭据 | 个人导出可在审计标签筛选；其余无入口 | 个人导出：已接入；另两项：缺后端 | 不做代用户导出；组织导出要限定受众、范围、到期时间、限流并审计，基于内容权限，经短时授权下载交付；已删账户需要行保留策略、回收，以及备份保留期的说明 | 个人导出：本人；其余：目标 `content.support_access` 类权限 + 租户批准 | 用户/组织/空间 | 高 | 账户进程；将来的导出作业和保留作业 | 只有获批的申请人能在到期前下载，过期后返回 410；策略窗口过后，已删账户不再有任何可达的行或 blob | 已上线（个人导出） / 未实现 |
| R13 数据请求 | 成员导入的到期清理（`SYNWEVO_MEMBER_IMPORT_CLEANUP`，默认关） | 查看、启用 | `ac/member_import_routes.py:239-268`；`core/member_import.py:777-785`；由 Sweeper 执行（`ac/app.py:51-62`） | 无（隐私页显示开/关） | 仅主机 | 纳入配置目录；预览首次清理会删除哪些内容；高风险确认；审计 | 主机；目标 `platform.config.manage` | 全局 | 高 | 账户进程的 Sweeper，每小时一次 | 启用后下一轮清理删除超过 7 天的导入 | 已上线（值待核实） |

#### 服务、队列与作业

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R14 服务 | 维护模式 / 全局只读；worker 暂停、排空、恢复（邮件、通知、桥接、Docs 队列） | 启用、停用；暂停、恢复 | 无（代码里没有维护标志） | 无 | 缺后端 | 各角色在服务端执行，豁免管理页、健康检查、OIDC 和恢复路径；worker 读取持久化的暂停标志，排队的行不丢；审计 | 目标 `platform.ops.execute` | 全局 | 高 | 将为共享的 HTTP 中间件和各 worker 循环 | 开启后业务写入返回 503，管理页正常；暂停期间邮件保持排队，恢复后每封只发一次 | 未实现 |
| R15 作业 | 周期作业（Docs maintain、账户 Sweeper） | 查看最近一次结果；立即运行 | `dw/jobs.py:682-730`；`ac/app.py:26-70` | 无 | 缺界面 | 持久化最近结果和错误；立即运行并写审计；TTL 纳入配置目录 | 目标 `platform.ops.execute` | 全局 | 低 | 两个进程各自的线程 | 立即运行返回的计数等于实际删除的行数 | 已上线 |
| R16 服务 | 各角色的就绪状态与全部后台 worker | 查看就绪；查看 worker 是否存活 | `core/http.py:1132-1215`；`core/workers.py:28-37`（只在本进程内）；readyz 只覆盖 job runner 和 mailer | 无 | 缺后端 | 健康板只读取配置里的源站；按进程汇总各 worker 状态 | 目标 `platform.ops.view` | 全局（每角色/进程） | 低 | 各角色的 `/readyz` | 停掉一个 worker 后，看板准确指出是哪一项检查失败 | 未实现 |
| R17 服务 | 队列积压总览 | 查看积压；说明发布为何被拒 | 目前只在部署时计数（`prod/receive.py:60-73` require_idle） | 无（只有邮件标签） | 缺后端 | 各队列的积压计数和最老一项的时长；"发布会因哪些积压被拒"视图 | 目标 `platform.ops.view` | 全局 | 低 | 各所属进程的只读查询 | 造一条失败的 webhook：总览显示 1 条待投，发布预检用同样的数字拒绝 | 未实现 |
| R18 服务 | 角色容器；服务日志 | 排空、重启、停止；查看日志 | 容器操作只能在主机上用 docker compose；`dw/collab.py:864-875` 在关停时写回；日志用 json-file 驱动 | 无 | 仅主机 | 受限主机执行器：只接受固定动作和参数，不开放 docker socket 或 shell；预检和复查；审计。受限的脱敏日志视图 | 主机；目标 `platform.ops.execute`、`platform.ops.view` | 全局（每角色） | 高 | 主机上的 docker compose | 重启后协同编辑还在，任务被恢复；日志中没有秘密 | 已上线（主机） |

#### 备份与恢复

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R19 备份 | 一致性备份 | 创建；计划、保留、加密、异地存放 | `scripts/platform.py:515` → `dws/snapshot.py:222`；生产环境只有部署前的 `cp -a`，备份从不清理 | 无 | 仅命令行 / 缺后端 + 缺外部配置 | 生产从不按计划备份；不写审计；备份未加密；没有异地副本；计划和保留策略需所有者决定 | 主机；目标 `platform.backup.execute` | 全局 | 高 | 主机上的 snapshot.py | 创建后 verify-restore 逐行通过；按计划运行，并按策略清理旧备份 | 已上线（CLI） |
| R20 备份 | 恢复点清单；恢复演练；部署前备份及其结果 | 查看；定期演练并记录 | `prod/receive.py:60-124`；`scripts/platform.py:526` → `snapshot.py:872` | 无 | 缺后端（需主机侧读取）/ 仅命令行 | 网页进程看不到备份目录，需要主机侧只读报告器；记录演练结果；部署时不写审计 | 主机 | 全局 | 高 | 主机 | 清单与主机上的实际目录一致；演练结果有记录 | 已上线（主机） |
| R21 恢复 | 数据及撤权状态 | 隔离恢复、受控切换 | 根脚本→Docs snapshot；core journal重放 | 仅主机CLI | 仅命令行，部分加固 | 无后台预检/作业/切换流程；`--no-journal`不能重建成员、账户、平台授权撤销，缺持久安全核对门禁；生产恢复仍需明确数据损失/写入协调 | 主机 | 整实例 | 高 | Store唯一迁移；安全日志撤销与事务收据 | `test_platform_grant_restore.py`、账户/组织恢复测试；新日志需兼容reader，细节见 [恢复说明](admin-operations.md) | 本批加固仅本地，未部署 |

#### 发布与域名

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| R22 发布 | 生产发布；CI/CD 与部署历史；各角色的运行版本；代码回滚 | 触发；查看状态与版本；回滚 | `.github/workflows/production.yml`；`prod/receive.py:109-158`；`/healthz` 按设计不返回版本号 | 无 | 缺外部配置（GitHub 令牌）/ 缺后端（版本）/ 仅主机（回滚） | 从后台触发需要 GitHub 令牌和环境审批；只有明确授权时才部署；认证后可见的版本与偏差比对；受控回滚要把代码回滚与数据回滚分开 | GitHub 权限 + production 环境；主机 | 全局 | 高 | GitHub Actions → 主机上的 receive.py | 各主机 `/readyz` 返回 200，各角色报告同一个 sha | 已上线（CI 流程） |
| R23 域名 | 五个正式主机与旧域名跳转；DNS、隧道、证书；边缘缓存；网关；主机基础设施文件的漂移 | 诊断；修改；清除缓存；查看漂移 | `prod/nginx.conf`；`prod/compose.yaml`；`docs/platform/domain-migration.md`；Cloudflare 在仓库之外 | 无 | 缺后端（诊断与漂移）/ 缺外部配置 / 仅主机 | 只探测配置里的主机，检查证书到期、跳转、issuer 和 RP ID 是否一致；文件哈希与仓库比对；DNS 修改需要受限令牌，并且绝不自动修改 | 主机；Cloudflare；目标 `platform.ops.view` | 全局 | 高 | 网关、主机、Cloudflare | 探针结果与 curl 一致；漂移哈希与仓库一致 | 已上线·本地有改动（clamd.conf 挂载需主机侧手动步骤） |

### 2.5 配置与治理

#### 配置中心

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| G1 配置中心 | 配置目录：约 126 个环境键 + 每个 IdP 6 个键 + `registration.mode` | 查看每项的有效值、来源、默认值、类型、敏感性、生效方式 | 没有注册表；零散的只读片段：`ac/cli.py:531`、`dw/cli.py:449`、`/api/auth/state`、`/api/v1/capabilities` | 只覆盖三个切片 | 缺后端 | 基于 `core/env.py` 的类型化注册表；各角色自报有效值；秘密一律遮蔽；显示旧名是否在用、是否与新名冲突；标出 D19 | 主机；目标 `platform.config.view` | 全局（每容器） | 中 | 各容器进程的环境 | 目录中的值与主机上 `docker compose config` 的结果和实际观察到的行为一致 | 未实现 |
| G2 配置中心 | 环境类配置 | 修改并应用；历史、回滚、漂移 | 应用内没有；目前由运维编辑文件后重建容器 | 无 | 仅主机 / 缺后端 | 有边界的执行器：白名单键、校验后的覆盖存储、只重建指定服务；显示每个实例的应用状态；expected 版本检查；拒绝覆盖 x-env 中的强制值；版本化设置表和回滚；漂移报告 | 主机；目标 `platform.config.manage` | 全局（每服务） | 高 | docker compose 重建 | 应用后受影响的角色报告新值，未触及的角色不变；回滚会产生一个新版本 | 未实现 |
| G3 配置中心 | 注册策略 `registration.mode` | 查看、修改；恢复继承 | `admin_routes.py:96-122`；`core/identity.py:851-865` | 注册策略标签 | 已接入·有缺陷 | D3；没有"恢复继承"；只能全局设置 | 读：PA；写：PA! | 全局 | 高 | 每次请求读取 meta 表 | 设为 invite 后注册返回 403，状态接口显示新值，审计有前后值（`test_admin_governance.py:55-72,274-310`） | 已上线 |
| G4 功能开放 | 功能开关框架 | 创建开关、限定范围、紧急关闭 | 没有 flags 表 | 无 | 缺后端 | 作用域继承、依赖检查、无需重启的紧急关闭、审计；在 API 层执行，不只是隐藏界面 | 目标 `platform.config.manage` | 全局/组织/空间 | 高 | — | 对空间 W 关闭后，界面隐藏，直接调用 API 也被拒；其他空间不受影响 | 未实现 |

#### 配置分项（目标：先进入 G1 的只读目录，再按需改为受控的动态配置或受控重建）

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| G5 配置 | 源站与 OIDC 身份（ISSUER、各类 URL、RP_ID、DOCS_RESOURCE 等） | 查看、修改 | `authc/rp.py:42-62`；`core/oidc.py:70-96`；`ac/passkeys.py:45-70`（均属于 CRITICAL） | 无 | 仅主机 | 只读诊断；修改仍由运维做；改 RP_ID 会使已有 Passkey 失效，改 issuer 会使已签发令牌失效 | 主机 | 全局（每服务） | 高 | 启动时读取 | discovery 结果正确，登录往返成功 | 已上线（值待核实） |
| G6 配置 | 信任边界（TRUST_PROXY、COOKIE_SECURE、CSP）；进程拓扑（ROLE、DATA_DIR 等） | 查看；修改 | `core/http.py:83,197`；`srv/run.py:53-67` | 无 | 仅主机；拓扑的修改：不适用 | 显示有效值，不安全时告警；拓扑决定后台自身运行在哪里，不从网页修改，但需要只读展示 | 主机 | 全局（每进程） | 高 | 各进程 | 响应头和 IP 记录正确 | 已上线（值待核实） |
| G7 配置 | 会话与令牌时长；限流与背压 | 修改 | `core/sessions.py:43-52`；`core/oidc.py:37-38`；`core/http.py:46,608` | 无 | 仅主机 | 需要数据库支撑的认证策略；限流桶按进程计数，界面上必须注明"每实例" | 主机 | 全局 | 高 | 各进程的模块常量 | 新会话按新值到期；请求超限返回 429 | 已上线（值待核实） |
| G8 配置 | 认证节流与 TTL 的代码常量 | 修改 | `ac/auth_routes.py:70-90`；`ac/registration.py:55-62`；`ac/email_login.py:34-43`；`ac/invites.py:25-33` 等 | 无 | 缺后端 | §7.11 要求这些可管理：迁入策略存储，在现有执行点读取；至少先只读展示 | 代码 | 全局 | 高 | 账户服务 | 改值后节流的阈值随之变化 | 未实现 |
| G9 配置 | 人机验证 Turnstile | 配置、轮换、测试、查看状态 | `core/challenge.py:48,84-95,175-207` | 无 | 仅主机 / 缺界面 | 脱敏状态、连接测试、轮换；失败汇总 | 主机 | 全局 | 高 | 账户角色 | 配置后，缺少 token 返回 400 challenge_required | 已上线（2026-09-25 的记录显示生产没有配置 key；值待核实） |
| G10 配置 | 恶意软件扫描与上传大小链 | 查看各角色的有效值和一致性；修改 | `core/malware.py:109-122`；`dw/engine/limits.py:33-36`；`prod/nginx.conf:9` | 无 | 仅主机·有缺陷 | D15；只读目录与一致性检查；扫描模式不能做成网页上的开关 | 主机；代码 | 全局（每角色） | 高 | 各角色启动时 | 超过上限的请求返回 413；EICAR 被拒 | 已上线（clamd.conf 挂载仅本地；值待核实） |
| G11 配置 | Docs 处理上限 | 查看、修改 | `dw/engine/limits.py:38-66` | 无 | 仅主机·有缺陷 | D14；遇到坏值应拒绝，而不是静默使用默认值 | 主机 | 应用（Docs） | 高 | docs 进程 | 超限时被拒；capabilities 报告的值一致 | 已上线（MAX_BLOCK_CELLS 仅本地） |
| G12 配置 | Docs 任务并发与配额 | 查看、修改 | `dw/jobs.py:399-490` | 无 | 仅主机 | 有效值目录；按空间覆盖见 B17 | 主机 | 应用（Docs） | 中 | Docs 的 job runner | 第 N+1 个任务返回 429 | 已上线（值待核实） |
| G13 配置 | Docs 引擎与路径；SOURCE_OFFER；IMPORT_ROOTS | 查看；选择引擎；开关 | `dw/engine/pdf_engine.py:32-47`；`dw/licensing.py:76`；`dw/api/v1.py:43-56` | 无（成员可在 Docs 看到） | 仅主机 | 只读展示；装有 PyMuPDF 时必须阻止关闭 SOURCE_OFFER；IMPORT_ROOTS 永远不能从网页编辑 | 主机 / 镜像构建 | 应用（Docs） | 高 | docs 进程与镜像 | about 和 capabilities 显示的值一致 | 已上线（值待核实） |
| G14 配置 | Webhook 投递与 SSRF 策略；机器令牌受众 | 查看、修改 | `dw/webhooks.py:103-206`；`core/machine.py:55-111` | 无 | 仅主机 | 有效值与上下界；开放私网地址时告警；受众检查的现状作为派生状态显示 | 主机 | 全局 | 高 | Docs 进程 | 默认拒绝私网地址；开启强制受众后返回 401 | 已上线（值待核实） |
| G15 配置 | 协同与消息相关的限值 | 查看、修改 | `dw/collab.py:109,128-129`；`core/messaging.py:169-183` | 无 | 仅主机·有缺陷 | D19；RECHECK_S 决定撤权后连接最多还能保留多久 | 主机 | 应用 | 中 | 各进程 | 超大帧被关闭；撤回时限生效 | 已上线（SHEET_LIVE_* 仅本地） |
| G16 配置 | 产品中硬编码的上限与治理预算 | 查看、修改 | `core/messaging.py:145-164`；`core/organizations.py`；`ac/invites.py` 等 | 无 | 缺后端 | 先决定哪些值是真正的策略旋钮；公开页面上的数字要与之同步 | 代码 | 全局 | 中 | 消息与账户角色 | 阈值随配置变化 | 未实现 |
| G17 配置 | 客户端、测试、开发用的变量 | 在后台管理 | SDK 和测试脚本 | 无 | 不适用 | 这些不是服务端配置 | — | — | 低 | 客户端 | — | — |
| G18 配置 | PLATFORM_NAME | 修改 | `core/config.py:26` | 无 | 仅主机 | 只部分生效，需要说明哪些界面跟随这个值 | 主机 | 全局 | 中 | 各角色 | 状态接口显示新值 | 已上线（值待核实） |

#### 安全与反滥用

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| G19 安全 | 限流状态 | 查看；带理由解除 | auth_attempts 表 | 无 | 缺后端 | 管理视图（地址哈希后显示）；解除操作写审计 | 目标 `platform.security.manage` | 全局 | 中 | auth_attempts | 解除后可以再次登录 | 未实现 |
| G20 安全 | 安全事件与告警 | 处理 | 未发现 | 无 | 缺后端（待核实） | 先确认确实不存在，再与待处理中心一起设计 | 目标 `platform.security.manage` | 全局 | 中 | — | 静音到期后告警自动恢复 | 未实现 |
| G21 安全 | 边缘与 WAF | 查看规则及误伤 | 在仓库之外 | 无 | 缺外部配置 | 需要受限令牌；机器调用、Webhook、OAuth 各自走自己的协议，不应被挑战 | Cloudflare | 全局 | 中 | 边缘 | 机器调用不会收到挑战页 | 待核实 |

#### 审计

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| G22 审计 | 平台安全日志 | 搜索、分页 | `admin_routes.py:544-570`；`core/store.py:236-252` | 审计标签 | 已接入 | 不能按时间、结果、对象筛选；没有对象时间线；request_id 没有关联到请求；不能导出 | PA | 全局 | 中 | audit_events | 管理动作的记录中有 reason | 已上线 |
| G23 审计 | 被拒绝的操作与敏感读取 | 记录 | 无（`_admin` 拒绝时不写记录） | 无 | 缺后端 | 写 denied 和 error 记录，并对记录本身限流；审计敏感读取 | — | 全局 | 中 | — | 非管理员访问时产生一条 denied 记录 | 未实现 |
| G24 审计 | 写操作与审计的原子性；事件结构 | 在同一事务中记录；前后差异 | 部分满足（D8） | 只显示原始数据 | 缺后端 | 同一事务或 outbox；动作目录；统一的前后值 | — | 全局 | 高 | admin_routes | 注入写入失败后，状态不变 | 未实现 |
| G25 审计 | 导出、保留、完整性 | 导出；清理；哈希链 | 无 | 无（正确：没有"清空"按钮） | 缺后端 | 受限导出（本身也要审计）；保留期的下限；完整性校验 | 目标 `platform.audit.export` | 全局 | 高 | — | 导出操作有记录；校验能发现被改动的行 | 未实现 |
| G26 审计 | 应用内写操作的审计缺口 | 补写服务端审计 | Docs 只有少数写操作有审计，webhook 完全没有 | 无 | 缺后端 | 操作者、目标、结果、理由 | — | 全局/空间 | 高 | audit() | 每个写操作恰好产生一条记录 | 未实现 |
| G27 审计 | 组织日志；本人活动；CLI 动作 | 读取；给平台的快捷入口 | `organization_routes.py:119-127`；`ac/account_routes.py:219-229`；`ac/cli.py:55-72` | 各自的界面 | 已接入 | 平台没有组织时间线；平台发起的操作没有标记；需要说明基础设施操作不在审计边界内 | 租户 / 本人 / 主机 | 多种 | 中 | audit_events | CLI 操作会产生 cli.* 记录 | 已上线 |
| G28 审计 | 安全日志文件 | 查看、校验 | `core/journal.py` | 无 | 仅命令行 / 缺界面 | 指示最近一次写入、失败情况和一致性 | 主机 | 全局 | 中 | 追加写文件 | 写入失败时显示 journaled=false | 已上线 |

#### 主站、品牌与政策

| 模块 | 资源 | 动作 | 现有服务与接口 | 当前后台入口 | 状态 | 缺失 | 所需权限 | 作用域 | 风险 | 执行方 | 验收方式 | 发布状态 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| G29 主站 | 主站文案；站点公告 | 编辑、发布；排期 | 文案写在代码里（`portal/public.py:59` 等） | 无 | 缺后端 | 版本化内容存储、防注入、预览与回滚、清缓存 | 代码；目标 `platform.site.manage` | 全局 | 中 | 门户 | 服务端回退页和 SPA 都已更新 | 未实现 |
| G30 主站 | 隐私与服务页面 | 带生效日期的版本；同意机制 | 无（页面写明不是正式条款） | 无 | 缺后端（需所有者先决定） | 所有者先定法律主体和正式条款 | 代码 | 全局 | 高 | 门户 | 新版本按生效日期提供 | 未实现 |
| G31 主站 | 运行事实 `/public/deployment.json` | 查看、刷新 | `portal/public.py:374,448` | 无 | 缺界面 | 在后台面板并排对照 | 匿名 | 全局 | 低 | 门户进程 | 60 s 内一致 | 已上线 |
| G32 品牌 | 正式 Logo | 替换、查看 | `scripts/brand_assets.py` | 无 | 仅命令行（随代码发版） | 只读显示；不做任意上传 | 代码 | 全局 | 中 | 构建流水线 | `--check` 通过 | 已上线 |
| G33 主站 | 边缘分析 | 启用、停用 | 在应用之外 | 无 | 缺外部配置 | 需要受限令牌 | Cloudflare | 全局 | 中 | 边缘 | 页面中的信标与 CSP 一致 | 待核实 |
| G34 其他 | 账单与支付；AI 供应商 | 任何管理动作 | 无 | 无 | 不适用 | 不建假模块 | — | 全局 | 低 | — | — | — |

## 3. 依赖与分批

### 3.1 批次 1：基础与第一条真实控制链路

对应 §12 第 1 项：恢复现场、建立能力表、打通管理员入口、权限与强认证、统一布局与审计，并交付一个真实账户操作，在业务侧验证它生效。

**B1-0 现场与能力表**

- 本文落到 `docs/platform/admin-coverage.md`。
- `iteration-state.md` 记录以下内容：
  - HEAD `e54b66e`，生产 `aadcb9e`，117 个提交尚未部署。
  - 本批的 schema 版本从 26 升到 27。
  - 本批验收的证据。

**B1-1 管理台外壳：升级现有的账户源 `/admin`**

- 保留：旧深链接、门户链接、404 伪装。
- 新增：
  - 五组分组导航，只显示有真实页面的组。
  - 导航由 `GET /api/admin/session` 驱动。
  - 环境徽标和"平台"范围标识。
  - 面包屑。
  - 三语、手机布局、键盘操作、减少动画。
- 本批不做：全局搜索、命令面板、保存视图。

**B1-2 委派平台权限模型**

- 新增 `synwevo_core` 权限目录（草案；实现时以代码中的目录为准并回写本表）：
  - **已实现（仅本地，2026-09-29）**：`packages/core/synwevo_core/platform_access.py` 是唯一目录，下表已回写。

| 权限 | 最高 | 账户与访问 | 支持 | 审计查看 | API 平台管理 |
|---|---|---|---|---|---|
| `platform.overview.view` | ✓ | ✓ | ✓ | ✓ | ✓ |
| `platform.users.view` | ✓ | ✓ | ✓ | ✓ | — |
| `platform.users.manage` | ✓ | ✓ | — | — | — |
| `platform.users.support` | ✓ | ✓ | ✓ | — | — |
| `platform.workspaces.view` / `.manage` | ✓/✓ | ✓/✓ | ✓/— | ✓/— | — |
| `platform.workspaces.claim`（复审新增：指派历史数据，理由见 `core/platform_access.py` 文档） | ✓ | — | — | — | — |
| `platform.orgs.view` | ✓ | ✓ | ✓ | ✓ | — |
| `platform.config.view` / `.manage` | ✓/✓ | ✓/— | — | ✓/— | — |
| `platform.mail.view` / `.manage` | ✓/✓ | ✓/✓ | ✓/— | ✓/— | — |
| `platform.audit.view` | ✓ | ✓ | — | ✓ | — |
| `platform.roles.view` | ✓ | ✓ | — | ✓ | — |
| `platform.roles.assign` / `.assign_super` | ✓/✓ | — | — | — | — |
| `api_platform.manage` | ✓ | — | — | — | ✓（仅由最高管理员显式授予，见 §4.1） |
| `platform.orgs.manage`（实现时新增，理由见 `core/platform_access.py` 文档） | ✓ | ✓ | — | — | — |

- 数据：新增 `platform_role_grants` 表。迁移时把现有 admin 转为最高管理员，同时保留 `users.platform_role` 并保持同步，方便回滚。
- 改动集中在一个共用入口 `require_platform(perm)`，需要接入的位置：
  - `_admin`
  - `core/http.py:835`
  - `require_manage`
  - `describe_user`
  - `auth_routes` 的两处
  - `platform_admins`
  - `profile.py:74`
  - `demote_first`
  - `organization_audit.py:155`
  - 前端的相关判断
- 规则：
  - 持有一项权限不等于能授予它；授予者只能授出自己持有的权限。
  - 被授予者必须满足前置条件（关闭 D2）。
  - 最后一名可用的最高管理员在同一事务中计数保护；两人并发互相降级时只成功一个。
  - 禁止自降级与自停用（§4.1）。
  - 授予时收紧被授予者现有会话的时限；撤权在下一个请求生效。
- `operator` 迁移为无权限，并从选择器中移除（§4.1）。
- 新增权限解释接口和名册页。
- TOTP/Passkey 重认证放到批次 2。

**B1-3 类型化管理操作与审计**

- 新增 `core/admin_ops.py`，按顺序执行：
  1. 授权
  2. 重认证
  3. L3 级操作校验预览 token
  4. 在事务内复读并检查版本
  5. 执行
  6. 在同一事务内写 ok 审计
- denied 和 error 单独写入，并做限流。
- 预览 token 绑定操作者、操作、目标、参数和版本，有效期 5 分钟。
- 使用幂等键。
- 迁移 `admin_routes` 的全部写操作，关闭 D8。
- 审计标签补齐筛选和对象时间线。

**B1-4 控制链路：单个账户停用与恢复（推荐）**

- 选它的理由：
  - 改变的是一个真实目标，业务侧的效果可以验证：会话、登录、委托令牌、Docs 协同 socket、Message 流、排队任务都会受影响。
  - 爆炸半径只有一个对象，而且可以撤回（恢复）。
  - 顺带修复高风险缺陷 D1 并补上测试。
  - 能一次覆盖 B1-2 和 B1-3 的全部要素。
- 不选其他候选的理由：

  | 候选 | 不选的理由 |
  |---|---|
  | 注册策略 | 影响全局，而且 `closed` 的语义还没定 |
  | 空间冻结 | 半径较大 |
  | 撤销会话 | 没有可以恢复的状态 |
  | 平台角色授予 | 它本身就是基础设施的一部分 |

- 具体改动：
  - 停用适用于 active 和 pending 账户；恢复只允许从 suspended 恢复。已验证的账户恢复为 active，未验证的恢复为 pending；其余情况返回 409。
  - 加入 expected_status 检查、影响预览、平台权限持有者保护、恢复说明。
  - 在详情抽屉中查看。
- 验收在隔离实例上完成：
  - 用委派的管理员身份执行停用与恢复。
  - 在另一个浏览器上下文中验证业务侧的变化。
  - 验证重试、预览和版本冲突。
  - 反向验证无权路径被拒。
  - 验证审计记录。
  - 最后跑完整的检查集。

**批次 1 的发布**

- 本地通过全部检查后，随下一批已验证代码推送部署（持续迭代约定已授权正常推送与必要部署；受 Actions 分钟预算约束）。
- 回滚时依靠 `users.platform_role` 保持兼容。

**批次 1 明确不做**

- 全局搜索和命令面板
- 重认证方式扩展
- 交接与应急恢复
- 配置中心
- 平台级的组织生命周期
- 业务模块、运维、备份

### 3.2 后续批次顺序与依赖

- **批次 2：身份、组织与配置**
  - 强认证扩展（D10）
  - 交接与应急恢复
  - 账户侧能力补齐
  - 组织与空间的平台路径
  - 配置中心：只读目录、D3、版本化设置、首批动态策略
  - 功能开关与应用目录（D6、D7）
  - 审计补齐
  - 权限模型扩展
- **批次 3：业务模块**
  - 前置：组合根适配器。
  - 顺序：API 平台（D4、D5、D16、D9、D20）→ Message（D13、D18）→ Docs 任务与引擎 → 文件与扫描 → 通知与邮件 → 协同 → 支持访问。
- **批次 4：运维与数据**
  - 前置：受限主机执行器和外部凭据。
  - 内容：看板、维护模式、主机执行器、备份、发布与版本、域名、存储。
- **批次 5：整体体验与发布**
  - 全局搜索、待处理中心、主站、视觉、回归测试、部署与复查。

各批之间的依赖：B1-2 和 B1-3 是所有后续操作的前置；G1 和 G2 是 B17、B20、B43、G8、G16 的前置；组合根适配器是 B11–B23、R2–R6 的前置；受限主机执行器是 R1、R18–R21、G2 的前置；保留期决定是 R9 和 G25 的前置；是否提供支持访问的决定是 B7、B8、B30、R12 的前置。

可以随任一批次单独修复的小缺陷：D4、D13、D12、D9、D16、D18、D20、D14。

## 4. 已定事项与需要所有者决定的事项

### 4.1 按专项约定、调研与现场核查已定（可复议，复议时改这里）

| 事项 | 决定 | 依据 |
|---|---|---|
| 管理入口 | 升级现有 `accounts.synwevo.com/admin`，保留 `?tab=` 深链接与 `/admin/api-platform`；不建 `workplace/admin`，不开新子域 | 专项约定 §1.1"优先升级现有管理员入口" |
| 生产最高管理员 | 2026-09-29 只读核查（掩码，不含秘密）：生产共 6 个账户，**唯一**有效平台管理员是所有者本人账户（li***@outlook.com），有口令与 3 个 Passkey、无 TOTP；无 operator；无未用的初始化令牌。迁移时把它映射为最高管理员；不按邮箱或注册顺序任命任何人 | 专项约定 §4.2；`docs/quality/deployment.md` 同日记录 |
| 最高管理员人数 | 至少 1 名由事务内不变量保证；只有 1 名时概览常驻"建议至少 2 名"提示，**不强制**（不能凭空要求第二人） | 调研 c1、c2；专项约定 §4.2 |
| 自降级与交接 | 最高管理员不能降级或停用自己；交接顺序是"先授予接任者 → 接任者登录并完成重认证 → 由接任者撤销原管理员" | 调研 c2、c3 |
| `operator` | 今天没有任何能力（D17）；迁移为无平台权限，从选择器移除，旧值只读显示 | 生产无 operator；D17 |
| 双人审批 | 本批不引入（现团队无法满足，专项约定明确不得凭空强制）；数据模型保留扩展位 | 专项约定 §4.2 |
| 重认证方式与窗口 | 沿用口令与 600 s 窗口；邮件验证码不能用于 step-up；B2已实现Passkey（UV）用途重认证；TOTP单独重认证仍待补齐（D10） | 现有 `sessions.py`；调研 b1 |
| `api_platform.manage` | 仍是平台权限，只经平台授予获得；迁移后最高管理员持有，另有内置"API 平台管理员"角色可由最高管理员显式授予；组织/空间角色永远不得到它 | CLAUDE.md；专项约定 §1.1 |

### 4.2 需要所有者决定（未决前按最保守的现状执行，不替所有者作答）

1. 第二名最高管理员由谁担任（建议至少 2 名，调研 c1）。
2. 管理员是否必须启用 MFA / Passkey，宽限期多长（当前所有者已有 Passkey，不会被锁）。
3. 应急恢复的保管人、使用后通知谁、多久演练一次。
4. `closed` 注册策略的确切语义（D3：是否也禁止邀请建号）。
5. 组织治理：平台能否冻结或删除非本人拥有的组织、孤儿组织由谁接手、是否需要平台级邀请、谁可以创建组织、已删除组织何时清除。
6. 是否提供支持访问（举报证据、Docs 内容），由谁审批、多长有效期。
7. 停用 messages 应用时的语义（D7）。
8. 未绑定审批的历史凭据如何处理（D5）。
9. 各类保留期：审计日志、内容、备份与异地副本、存储配额、消息。
10. 配置修改的方式（是否允许后台触发受限主机执行器重建容器），以及哪些代码常量改为可配置。
11. 是否提供外部凭据：GitHub 只读令牌、Cloudflare 受限令牌、发信服务商 API、授权的真实测试收件箱。
12. 法律主体与正式条款；主站文案是否改为在后台编辑。
13. 部署：批次 1 含 schema 迁移，随下一批已验证代码一起发布（受 Actions 分钟预算约束，见 `docs/quality/deployment.md`）。
14. 是否需要平台公告功能。

## 5. 维护本表

- 每批完成时更新本表的状态、证据和发布状态。
- 新增能力先在本表登记。
- 本表不写秘密和用户正文。
