# 身份迁移：v4 升级、历史数据、备份与回退

这份文件回答四个问题：升级时数据会怎样、账户体系建立之前的数据归谁、怎么备份、出事了怎么退回去。

**结论先说**：v4 及其之后的迁移**不改写任何一行业务数据**。`workspace_id` 就是 `tenant` 值，
所以让历史数据归属一个工作空间只需要写入一行工作空间记录，不需要动 69 个项目中的任何一个。

---

## 1. 迁移模型

`apps/docs/synwevo_docs/store.py: _migrate()`。**只向前、可重复执行、纯追加**。

```
schema v1  →  v2  （tenant / lease / runs / events / idempotency / api_keys / webhooks）
           →  v3  （projects.origin、projects.archived_at）
           →  v4  （身份：users / sessions / workspaces / memberships / …）
```

两种追加方式，触发条件不同：

| 方式 | 何时执行 | 例子 |
|---|---|---|
| `SCHEMA_V2` / `SCHEMA_V4` 的 `CREATE TABLE IF NOT EXISTS` | **每次打开数据库**，无条件 | 平台表（`apps`、`oauth_*`、`passkeys`…） |
| `V2_COLUMNS` / `V3_COLUMNS` 的 `ALTER TABLE ADD COLUMN` | 仅当 `ver < SCHEMA_VERSION` | `projects.origin` |
| `V4_COLUMNS` | **每次打开数据库**，无条件 | `oauth_tokens.family_id`、`sessions.oidc_sid` |

> `V4_COLUMNS` 必须无条件执行，这是一个踩过的坑：v4 的表本身是无条件创建的（纯追加，不需要提版本号），
> 所以一个已经标记为 v4 的数据库**永远不会再进入 `ver <` 分支**。把这些列放进版本门控的列表里，
> 等于对每一个已存在的安装静默跳过它们。

因此：**新增一张表不需要提版本号；给 v4 的表新增一列，必须加进 `V4_COLUMNS`。**

### 1.1 迁移由谁执行

由**任何一个打开数据库的进程**执行，在 `Store.__init__` 里。三个角色（accounts / portal / app）
共用同一个 SQLite 文件，因此共用同一份迁移代码——不存在两套冲突迁移，也不存在两个账户权威来源。

并发安全：`Store.tx()` 是 `BEGIN IMMEDIATE`，外面还有一层进程写锁。三个进程同时启动时，迁移串行执行，
且每一步都是幂等的。

---

## 2. 身份映射：没有新的 ID

- **用户 ID 保持不变**（`u_…`）。没有引入"中央 ID"，因此不存在映射表，也不存在外键、资源归属和
  审计关联被切断的风险。
- **`workspace_id` 就是 `tenant` 值。** 没有另造 `org_id`，也没有按应用给同一个租户重新编号。
- 跨应用身份通过验证过的 `issuer + subject` 关联；`sub` 就是上面的用户 ID。
- **邮箱不是身份主键。** `users.email_key` 是规范化后的唯一约束，但邮箱可变；不会因为两个账户的
  邮箱字符串相同而自动合并。

---

## 3. 历史数据：workspace `default`

### 3.1 现状

账户体系建立之前写入的每一行都带 `tenant='default'`。迁移时 `_seed_legacy_workspace()` 写入一行：

```
workspaces: id='default', kind='legacy', status='pending_claim', 成员 0 个
```

**零成员是重点，不是疏漏。** 访问权由成员关系授予，所以在平台管理员显式指派之前，**没有人**能
读到这些文件——包括平台管理员自己，包括第一个注册的人。

真实库上的实测（`tests/integration/migration_rehearsal.py`，69 项目隔离副本）：

```
default 空间成员数 : 0
default 空间项目数 : 68        （另有 1 个已删除）
管理员能进 default 吗 : False —— 你不是该工作空间的成员
```

### 3.2 什么都不会自动认领它

以下**都不会**让任何人获得 `default`：

- 注册 / 首次登录
- `manage.py bootstrap admin` 建立首位平台管理员
- 应用接入、工作空间创建、应用资格迁移（`adopt_existing_members()` 显式跳过它）
- 未选择工作空间时的会话——tenant 落到 `__no_workspace__<uid>`，一个没有任何行携带的 id，
  **绝不回落 `UI_TENANT`**（`UI_TENANT` 正是 `"default"`）

### 3.3 显式认领

`POST /api/admin/workspaces/{wid}/claim`，界面在账户服务的 `/admin`。约束：

1. 目标必须是**已经存在**的账户——这一步不创建账户；
2. 需要 `scope=admin` 且 **strict** 的近期重认证（刚登录不算，必须为这件事重新输口令）；
3. 需要**书面理由**（≥8 字），原样写入审计；
4. **一个事务**内完成成员关系、空间状态、应用启用与使用资格；
5. **不改写任何一行业务数据**——项目仍带 `tenant='default'`；
6. 只作用于指定空间，**不顺带授予平台管理权**，也不给执行认领的管理员任何读取权；
7. 只有 `kind='legacy' AND status='pending_claim'` 的空间可被认领。普通工作空间会被 409 拒绝——
   这个路由是给账户体系之前的数据用的，不是进入任何人工作空间的通道。

认领成功后，系统生成的名字"历史数据（待认领）"会被替换（默认"历史数据"），因为那个括号在认领
之后就不再为真，而它每天都显示在工作空间切换器里。管理员自己改过的名字不会被覆盖。

审计记录包含：操作者、目标账户与邮箱、角色、理由原文、受影响项目数。

---

## 4. 旧认证模式

`SYNWEVO_UI_AUTH` 有四个值。它们是**共享口令，不是身份**：

| 模式 | 含义 | 平台模式下的边界 |
|---|---|---|
| `none` | 无任何访问控制（默认值） | 仅限本机开发。租户固定为 `UI_TENANT` |
| `basic` | HTTP Basic 共享口令 | `dw.wegreen.ltd` 今天用的就是它 |
| `proxy` | 由反向代理负责 | 同上 |
| `session` | 真正的账户体系 | 平台模式必须用这个 |

**为什么保留旧模式**：`basic` 是现有部署正在运行的模式，把它从一个活着的实例底下抽走以便上线
账户体系，正是那种会让人丢失访问权的迁移。

**边界**：`basic` / `none` 下没有用户身份，因此也没有工作空间、没有应用资格、没有 MFA。它们
不是"绕过中央策略的通道"，而是"另一种部署形态"——同一个进程不可能同时处于两种模式。启用平台
模式（`session`）后，这些旧入口就不再存在于该进程上。

OIDC 协议端点（`/oidc/*`、`/.well-known/*`）和 SSO 端点（`/auth/*`）**不经过旧的 Basic 关卡**：
它们各自携带自己的认证，而在一条重定向链中间弹出 HTTP 认证对话框不是登录流程。

---

## 5. 备份

### 5.1 必须包含什么

| 内容 | 位置 | 为什么 |
|---|---|---|
| 数据库 | `data/docweave.sqlite3` | 账户、工作空间、项目、任务、审计 |
| blob 内容 | `data/blobs/`（内容寻址目录，实测 695 个文件） | 文件正文**不在库里** |
| 签名私钥 | `oidc_keys.private_jwk`（**在库里**） | 丢了 = 所有已签发令牌失效 |
| MFA 种子密钥 | `SYNWEVO_SECRET`，或库内 `meta.server_secret` | 见下 |

> **必须用 SQLite 的 backup API，不能 `cp`。** 这不是"可能落后一点"的问题。在**今天这个真实库**上
> 实测：
>
> | 打开方式 | 能看到的表 |
> |---|---|
> | 正常只读打开（读 WAL） | **44 张** |
> | 只复制 `docweave.sqlite3` 主文件 | **16 张** |
>
> 丢掉的 28 张表里包含**整个身份体系**：`users`、`user_passwords`、`sessions`、`workspaces`、
> `memberships`、`totp_secrets`、`recovery_codes`、`passkeys`、`oauth_clients`、`oauth_tokens`、
> `oidc_keys`、`audit_events`、`blob_grants`……它们全都还在 WAL 里，从未被 checkpoint 进主文件。
>
> 也就是说：**对这个实例执行 `cp data/docweave.sqlite3`，恢复出来的是一个一个账户都没有的安装。**
> 而且它能正常启动——迁移会把这些表重新建成空表——所以失败的样子是"所有人都没有账户了"，
> 不是"备份文件损坏"。
>
> `apps/docs/scripts/snapshot.py` 与 `tests/integration/migration_rehearsal.py` 都用 backup API，因此不受影响
> （快照验证里 44 张表全部恢复，20/20 通过）。

### 5.2 密钥的实情

`manage.py info` 会如实报告：

```
MFA 种子密钥 : 取自库内 meta.server_secret —— 与密文同库，
               拿到数据库副本即可解出 TOTP 种子；建议设置 SYNWEVO_SECRET
```

这句话是有意保留的。**把密钥和它加密的密文放在同一个文件里，不叫加密。** 设置外部
`SYNWEVO_SECRET` 之后，数据库副本本身不足以解出 TOTP 种子；在那之前，一份数据库备份就是一份
可解密的 MFA 备份，必须按密钥级别保护。

签名私钥同理：它就在 `oidc_keys` 表里。

**备份文件本身必须被当作密钥材料保护，不得提交进仓库。**

---

## 6. 恢复：三件必须防住的事

恢复一份旧备份不是"回到过去"那么简单。按 §6.8 的要求，至少防住：

| 风险 | 为什么会发生 | 怎么防 |
|---|---|---|
| 已撤销凭据复活 | 备份里那条 `revoked_at` 还是 NULL | 恢复后立即 `UPDATE sessions/oauth_tokens SET revoked_at=…`，或轮换签名密钥使全部令牌失效 |
| 任务重复执行 | 备份里的任务还是 `running`/`queued` | 恢复后把未完成任务标为需要人工裁决，不要让 worker 直接接手 |
| 旧的无认证模式重新暴露 | 备份的运行配置是 `SYNWEVO_UI_AUTH=none` | 认证模式来自**环境变量，不在数据库里**——恢复数据库不会改变它。但恢复整台机器会，必须单独核对 |

此外：已认领的工作空间恢复到认领之前，会回到 `pending_claim/0 成员`，需要重新认领。这是正确的
（审计记录也一并回退了），但要知道会发生。

---

### 6.1 验证会抓到什么（一次真实的例子）

收尾验证时我用 `sqlite3.backup()` 复制了真实库，但**忘了复制 `data/blobs/`**，然后对那个目录
做了快照。`verify` 给出 19/20：

```
  ✗ 被引用的 blob 全都在：694 个
```

这正是该检查存在的理由——恢复出一个"目录齐全、正文全无"的实例是最糟的失败方式，因为它看起来
完全正常，直到有人去打开一个文件。检查在这里抓住的是我自己搭测试环境时的疏忽，但生产上同样
的疏忽（只备份数据库、忘了对象存储）是一模一样的形状。

## 7. 回退

**不要假装所有 schema 升级都有无损降级。**

| 方向 | 可行性 |
|---|---|
| 代码回退到旧版本，数据库保持新 schema | **可以**。迁移是纯追加的；旧代码看不见新表和新列，忽略它们即可正常运行 |
| 数据库降级（删除新表/新列） | **不支持**。没有实现降级脚本，也不打算有——它只会丢弃账户、会话与审计 |
| 从备份整体恢复 | 可以，见 §6 |

实际的回退办法是**回退代码**，数据库留在原地。这对本轮的全部改动成立，因为没有任何一次迁移
改写过既有行。

---

## 8. 本机演练

```bash
python tests/integration/migration_rehearsal.py --from data          # 隔离副本，来源只读
python tests/integration/migration_rehearsal.py --from data --keep   # 保留副本以便检查
```

它做的事：一致快照 → 在副本上跑迁移 → 逐行指纹比对 → 检查新列 → 检查 `default` 仍未认领 →
重复执行确认幂等 → 确认来源未被改动。

**在真实 69 项目库上的实测结果：15/15 通过。** 项目、模板、任务、产物逐行指纹不变；
`default` 仍 `legacy/pending_claim/0 成员`；来源数据库修改时间未变。

---

## 9. 相关

- 会话、SSO、撤权：[sessions.md](sessions.md)
- 权限契约（平台角色、应用资格、逐对象授权）：[permissions.md](permissions.md)
- 本机启动：[../development/local-platform.md](../development/local-platform.md)
- 测试：`apps/docs/tests/test_migration_v4.py`、`test_admin.py`（认领流程 21 条）
