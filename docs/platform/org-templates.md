# 组织共享模板（CI-35）

9.8「组织模板复用」：个人模板与组织共享模板的权限、版本、维护者和使用方式清楚；发布/更新/弃用保留旧任务可重现性，不因模板更新改变正在执行任务的含义。

2026-10-06（CR-045）：初次发布与发布新源版本在同一事务内检查当前源空间成员、Docs 内容资格、导出权、整个组织的可见范围和数据流政策，再读取并冻结当前 head。`none` 拒绝；`audience` 要求组织的全部当前有效成员均已属于源空间；同组织 `organization` / `any` 允许。组织管理权本身不授予来源内容或导出权。既有冻结发布及已复制模板保持组织/目标所有权，来源撤权、删除或之后收紧政策不逆向撤销它们。只有说明且无新 head 时仍可维护；已有 update 端点遇到新 head 会一并发布，因此整次操作仍须源权限。见 [data-flow.md](data-flow.md) 与 `tests/integration/test_org_template_transfer_policy.py`。

## 1. 决策记录

| 项目 | 结论 |
|---|---|
| 问题与基线 | 模板只属于一个工作空间（`templates.tenant`）：同一组织的另一个空间要用同一份合同模板，只能把文件再传一遍、再划一遍字段；改了以后两边各自漂移，也没人知道哪一份是「标准」。个人复制审批（CI-27）解决的是整个项目进组织，不是模板的持续复用。 |
| 成熟参考 | Microsoft Learn「Create an organization assets library」（2026-09-26 读取）：管理员把一个文档库指定为组织模板库，用户在 Office 新建时看到「你的组织」标签页；上传的必须是模板文件（.dotx/.xltx/.potx），用户需要对站点有读权限，最多 30 个库。Google Workspace 管理员帮助「Turn custom Drive templates on or off for users」（同日）：组织模板库三种模式——开放（任何人可增删）、审核（持「Docs Templates」权限的管理员逐个批准）、限制（只有该权限的管理员可提交）；可配置分类。两处都没有写明模板更新后已创建的文档会怎样——因为它们的模板是「新建时复制一份」，文档与模板之间没有活链接。 |
| 对比与选择 | (a) 组织级模板表，项目直接引用它：项目、映射、任务都要学会一种新的模板归属，读取时要跨空间授权，任务快照、blob 可达性、模板编辑器全部要改；(b) 把空间模板的 `tenant` 改成组织：破坏「空间是租户」的边界，编辑权也说不清；(c) **本方案**：发布 = 把空间模板 head 版本冻结成组织的一次「发布」（文件、字段、元数据都复制进发布记录）；使用 = 在读者自己的空间复制成一份普通模板并记录来源。与 Office/Google「新建时复制」一致，下游（映射、任务、版本、审批复制）不用学新东西，任务可重现由构造保证。发布权 = 源空间的编辑者（与改模板同权），维护者 = 源空间编辑者与组织 owner/admin；不做审核队列（记录为未做）。 |
| 预期变化 | Docs 首页模板库：组织空间里的模板行多出「发布到组织…」；已发布的行显示「已共享 · 第 n 次发布」，菜单变为「更新组织发布」（源模板有新版本才可用）、「弃用共享…」/「恢复共享」。首页新增「组织共享模板」区（组织空间可见）：名称与说明、格式、字段数、第几次发布/模板第几版、来源空间、维护者、已用于几个空间；「复制到本空间」，维护者有 ⋯ 菜单。项目「设置模板」对话框多出「从组织共享选择（n）」页签：使用即复制到本空间并绑定项目。 |
| 边界与风险 | 权限：浏览任何组织成员可以（读路由 `None`），发布/更新/弃用/使用是写路由（viewer 403）；服务层再核对当前空间属于该组织且调用者是组织成员（否则 404，不区分「有但无权」）；维护另要求源空间编辑者或组织 owner/admin（403）。个人空间发布 → 400 `no_organization`。冻结：发布记录自带 `working_blob`/`slots`/`info`，源模板之后的编辑、删除都不影响发布与已复制的副本；源模板删除后现有发布仍可使用，「更新发布」答 409 `source_gone`。发布记录的 blob 不进入 `authz._REACHABLE`：组织成员看到的是名称/格式/字段数/说明，预览要先复制到本空间。同一模板同一组织只有一条发布（409 `already_published`）；重复弃用/恢复 409。说明 ≤500 字。审计：`docs.template.publish/release/deprecate/restore/use_shared`。不发收件箱通知（每次发布通知全组织是噪音；记录为未做）。三语言 31 条；手机：对话框页签与表格在 390 px 无横向滚动。 |
| 验收 | `tests/integration/test_org_templates.py`（viewer 403、个人空间 400、发布冻结 v2、重复 409、另一空间可见且不可维护、另一组织 404、viewer 不能复制、复制得到带来源的本空间模板并绑定项目、源模板加字段不影响发布与副本、显式更新发布 → 第 2 次/第 3 版、未变 409、发布历史、弃用后隐藏/拒绝使用/副本无恙、组织 owner 可恢复、源删除后仍可用且更新 409）；浏览器 `ui_e2e_org_templates.py`（Chromium + WebKit，成员在手机）。 |
| 发布与恢复 | schema v18：`organization_templates`、`organization_template_releases` 两张表，增量；回滚到 v17 代码时表闲置，已复制的副本是普通模板不受影响。 |

## 2. 接口

| 接口 | 权限 | 说明 |
|---|---|---|
| `GET /api/organization-templates?include_deprecated=` | 空间成员（组织成员） | `{organization: {id,name} \| null, items}`；个人空间 `organization=null` |
| `GET /api/organization-templates/{id}` | 同上 | 含 `releases`（最近 50 次） |
| `POST /api/templates/{tid}/publish` | 编辑者，组织空间 | `{description?}`；201；冻结 head 版本为第 1 次发布 |
| `POST /api/organization-templates/{id}/update` | 维护者 | `{description?}`；源模板 head 比发布新则产生新一次发布；否则只改说明，或 409 `unchanged`/`source_gone` |
| `POST /api/organization-templates/{id}/deprecate` | 维护者 | `{deprecated?: bool, note?}`；弃用后不再列出、不可使用 |
| `POST /api/organization-templates/{id}/use` | 编辑者 | `{name?, project_id?, base_settings_rev?}`；201 本空间的新模板（`info.origin` 记录发布与次数），可同时绑定项目 |

模板列表项（`GET /api/templates`）多出 `publication`：`{id, release_no, version_no, deprecated, updated_at, use_count}` 或 `null`。

**机器 API（CI-36）**：同一能力在 `/api/v1` 下——`GET /organization-templates`（分页）、`GET /organization-templates/{pid}`、`POST /templates/{tid}/publish`（幂等）、
`POST /organization-templates/{pid}/releases`、`POST /organization-templates/{pid}/deprecate`、`POST /organization-templates/{pid}/use`（幂等，可绑定项目）。
初次发布或包含新源版本的更新，委托还必须绑定源工作空间，且当时仍有该空间 Docs 写入资格；其他空间的委托不能借由委托人本人的跨空间权限读取新源内容（403 `source_delegation_required`）。已冻结版本的使用及没有新源内容的维护沿用既有组织权限。

全部要求**委托令牌**（代表某个成员的客户端）：组织成员身份属于人，服务账户与 API 密钥没有它，答 403 `delegated_required`，不做静默降级。
Python SDK：`list/iter/get_organization_template(s)`、`publish_template`、`release_organization_template`、`deprecate_organization_template`、`use_organization_template`；
CLI：`synwevo-docs org-templates list|get|publish|release|deprecate|restore|use`。

## 3. 明确未做

* 发布审核队列（Google 的「审核」模式）与分类。
* 发布/更新的收件箱通知。
* 在复制前预览共享模板的页面；副本里「源发布有新版本」的提示（副本是普通模板，本轮只记录来源）。
* 跨组织共享、平台级模板市场。
