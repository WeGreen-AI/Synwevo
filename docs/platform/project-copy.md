# 个人项目复制到组织工作空间（CI-27）

一个人在自己的个人空间里搭好的 Docs 项目（来源文件、模板与字段、样本规则、映射）可以申请复制到
他所在组织的某个工作空间。**来源政策与目标审批同时生效**：申请要由一位持有 `org.approvals.decide` 且是目标空间
admin/owner 的成员批准；批准后由 Docs 的执行器创建一份独立的副本。原项目从头到尾不被写入，
审批人只看到申请人确认过的清单，看不到申请人的个人空间。

2026-10-06（CR-045）：申请在保存清单和通知目标前检查来源数据流政策；批准时及执行器认领后的复制事务内再次检查。政策拒绝时 HTTP 403 `transfer_*`，已批准但尚未执行的申请转为 `stale` / `transfer:<code>`，不创建副本。拒绝与撤回继续可用。已合法完成的独立副本不因来源之后关闭导出而失效。UI、机器 API 和执行器复用同一契约，详见 [data-flow.md](data-flow.md)。

## 1. 决策记录

| 项目 | 结论 |
|---|---|
| 问题与基线 | 个人空间里的项目只能留在个人空间；要给团队用只能在组织空间里重新上传、重新设字段与映射（`projects`、`templates`、`source_files`、`mapping_revs` 都带 `tenant`，没有跨租户的移动或复制入口）。组织侧也没有任何「接收」的把关：如果直接允许成员往组织空间写入，组织空间的管理员对进入的内容没有决定权。 |
| 成熟参考 | Google Drive「Move files to shared drives」（support.google.com/a/users/answer/9310156，2026-09-26 读取）：把 My Drive 里的文件移入共享云端硬盘需要对该共享云端硬盘有 **Contributor 及以上** 权限，移入后文件归团队而非个人所有——目标侧的权限决定谁能往里放东西，放进去就是团队的。GitHub「Transferring a repository」（docs.github.com，读取同日）：转到组织要求发起人 **在目标组织有创建仓库的权限**，页面逐项列出转移后随之改变的内容（协作者、issue 指派、包与赞助关联），转给个人账户则要收件人确认——目标侧把关 + 转移前说清「带什么、不带什么」。OWASP ASVS 4.0.3 V4（github.com/OWASP/ASVS，读取同日）：V4.1.1 访问控制在可信服务层执行、V4.1.3 最小权限（只能访问被明确授权的资源）、V4.2.1 对记录的创建/读取/更新/删除都防 IDOR。未查到 Notion「Move to」的现行官方页面（两个候选地址均 404），不作为依据。 |
| 对比与选择 | (a) 直接「移动」：个人项目的 tenant 改成组织空间——原件消失、任务/产物/凭据的归属会跟着改变，一旦目标空间之后把人移除，本人连自己的成果都丢了；否决。(b) 「导出再导入」：把项目打成包再上传——两次走文件接纳链、丢失映射与模板版本语义，并且绕不开「谁能往组织空间放东西」的问题；否决。(c) **申请 + 目标审批 + 服务端复制**（本方案）：借 GitHub 转让「目标侧权限把关 + 事先列清带与不带」的形式，借 Drive「目标侧 Contributor 及以上才能放入、放入即归团队」的边界，并比二者更进一步——由目标空间的管理员逐项批准；复制而非移动（原件不动、来源 blob 按内容哈希共享、其它对象全部新建）。保留：Docs 现有的项目/模板/来源/映射结构与工作空间授权（`authz.py`），账户服务现有的组织权限目录与敏感操作重认证。 |
| 预期变化 | Docs 项目菜单新增「复制到组织工作空间…」（仅个人空间的项目、仅所在者）：选目标空间（只列本人是编辑及以上、组织活跃的空间）、副本名、用途、是否带来源文件/修正，对话框实时显示将被复制的清单；申请后目标空间的可决定者收到收件箱事件与邮件，组织页新增「审批」区（`?approval=<id>` 直达），批准/拒绝要写理由并重新验证口令；批准后 Docs 执行器 10 s 内创建副本，申请人收到通知，对话框里「切换到目标空间并打开副本」。若申请人本人就是可决定者，可在申请时勾选「批准并复制」，记录为自行批准。 |
| 边界与风险 | 权限：申请需要来源个人空间 owner + 目标空间 editor 及以上 + 目标组织成员；决定需要 `org.approvals.decide` + 目标空间 admin/owner + 组织活跃；执行前把这三方面全部重查（`preconditions`），任何一项丢失都不复制。内容：清单里只有名称、大小、哈希、数量与扫描状态，不含文件正文；批准的是清单摘要（`digest`），执行时重算，来源或模板/映射/规则变了就标 `stale` 要求重新申请。数据：一次事务写目标租户；来源 blob 共享（内容寻址、已经过接纳链），`source_files` 新 id，`templates`+`template_versions` 新建 v1（保留 `scan`），映射按新 id 重写、指向未复制来源的映射覆盖被丢弃并在结果里 `warnings`；任务、产物、上传记录、blob 授权、Webhook、API 凭据、来源提供记录一律不复制。时效：待审批 7 天过期，批准后 7 天不执行过期。三语言：Docs 32 条、组织页 45 条。移动端：审批区在 390 px 上完整可操作（浏览器证据）。 |
| 验收 | §5 表；接口测试 `tests/integration/test_project_copy.py`（2 项，真实组合服务器 + 真实任务读回）；两引擎浏览器闭环 `ui_e2e_project_copy.py`；负向：目标 viewer 申请 403、非组织成员 404、持权限但非目标空间管理员的组织管理员 403、未重认证 `reauth_required`、理由过短 `reason_required`、重复申请 409、来源变更后 `stale`、撤回后不能再决定、组织空间项目不能作为来源。 |
| 发布与恢复 | schema v13 只新增 `project_copies` 一张表，无数据迁移。回滚到旧版本：表被忽略，已创建的副本是普通项目不受影响；未执行的批准会在旧版本下停留在 `approved`，升级回来后若仍在 7 天内会被执行（执行前重查授权与摘要）。执行器每角色一个线程；多实例部署时 `claim_approved` 的条件更新保证一份申请只被执行一次，`reset_stale` 的条件更新保证后到者不会改写别人认领或已完成的申请。 |

## 2. 对象与状态

`project_copies`（核心库，`synwevo_core.project_copies.ProjectCopies`）：`source_workspace_id`/`source_project_id`/`requester_id`、
`target_organization_id`/`target_workspace_id`、`name`/`purpose`/`scope`、`manifest`（申请时的清单）/`digest`、`status`、
`decided_by`/`decided_at`/`decision_reason`/`self_approved`/`approval_expires_at`、`executed_at`/`result`/`error`、`version`（乐观锁）、
`expires_at`（待审批过期）。

```
pending ──approve──▶ approved ──claim──▶ running ──▶ done
   │                    │                    └──▶ failed（复制出错，可再申请）
   ├──reject──▶ rejected │
   ├──withdraw─▶ withdrawn（approved 未执行时也可撤回）
   └──7 天────▶ expired  └──7 天 / 重查失败──▶ expired / stale（source_changed、approval_expired、source_authority_lost、target:*、decider_authority_lost、organization_unavailable）
```

清单（`manifest`）：项目（名称、步骤、rev、样本规则、输出设置）、模板（名称、版本号、是否固定、字段数、工作副本哈希、扫描状态）、
映射（修订、条数、启用数）、来源（数量、总字节；勾选带来源时逐个列出文件名/大小/格式/哈希/版本/扫描状态/有无 OCR 与修正）、
`excluded` 固定列出不复制的对象。`digest` = 项目 rev/模板/版本/映射修订/规则/输出 + 模板版本与字段 + 来源 id/哈希/版本的规范化哈希；
改名不影响，改规则、改映射、换模板、增删来源都会让它变化。

## 3. 谁能做什么

| 动作 | 条件 | 否则 |
|---|---|---|
| 看到目标列表 `GET /api/copy-targets` | 当前空间是个人空间；列出本人为 editor 及以上、组织活跃的组织空间；`can_self_approve` 标出本人也能决定的空间 | 空列表（不是错误） |
| 申请 `POST /api/projects/{pid}/copy-requests` | 项目在个人空间且本人是该空间 owner；目标可写；同一项目对同一目标只能有一个未完成申请 | 409 `source_not_personal` / 403 `source_owner_required` / 403 `target_write_required` / 409 `copy_already_requested` |
| 申请时自行批准（`decide: "approve"` + `reason`） | 本人满足决定条件 | 403 `forbidden` |
| 撤回 `POST /api/copy-requests/{cid}/withdraw` | 申请人；状态 pending 或 approved 未执行 | 409 `not_withdrawable` |
| 列出/查看 `GET /api/auth/organizations/{oid}/approvals[/{aid}]` | 持 `org.approvals.decide` 且仍是目标活跃空间成员的活跃组织成员（`can_decide` 逐项标出是否还需管理员身份）；查询在分页限制前按目标成员身份过滤，成员撤销或空间停用/删除后列表不返回、详情404 | 403 / 非成员 404 |
| 决定 `POST …/approvals/{aid}/decide` | `org.approvals.decide` + 目标空间 admin/owner + 近期重认证（`scope=admin`）+ 理由 ≥ 4 字 + 可选 `version` | 403 `forbidden` / 403 `reauth_required` / 400 `reason_required` / 409 `not_pending` / 409 `version_conflict` |

平台管理员不因平台角色获得任何一项；组织 owner/admin 的基础集合含 `org.approvals.decide`，但仍必须是目标空间的管理员。
审批人拿不到源项目：`GET /api/projects/{pid}` 在目标空间上下文里对源项目是 404，清单里也没有文件正文或下载入口。组织管理权不会单独开放清单读取；来源政策计算的目标成员范围与审批列表/详情的实际读者保持一致。

## 4. 执行（Docs，`synwevo_docs.copies`）

`CopyRunner` 每 10 s 一轮（自行批准时立即唤醒）：先 `sweep()` 过期，再逐个 `next_approved()`：

0. 轮到它时已不是 `approved` 的申请（另一个执行者已认领或已完成，或被列出后撤回/过期）原样返回，不判定、不通知。
   自行批准会唤醒执行线程，而另一轮可能已把同一申请列为 approved：后到的执行者曾把 `status:running`/`status:done`/`status:withdrawn`
   当作前提失败，把进行中或已完成（副本已在目标空间）的申请改成 `stale` 并告诉双方失败。
1. `preconditions()`：批准时效、来源方仍是个人空间 owner、申请人仍可写目标、决定者仍有决定权、组织活跃；任一失败 → `stale`，原因写入 `error`。
   `reset_stale()` 是条件更新 `approved → stale`：申请在检查期间被别人认领或撤回时不写、不通知。
2. 在来源租户下重建清单并比较 `digest`；不同 → `stale: source_changed`。
3. `claim_approved()`（条件更新 `approved → running`，多实例只有一个成功）。
4. 一次事务：新模板 + v1（共享工作副本 blob，复制 `scan`）、新项目（`origin=copy:<cid>`，若原项目固定了模板版本则固定到 v1，`mapping_rev=1`）、
   勾选时新 `source_files`（新 id、共享 blob/解析结果，`ocr`/`corrections` 仅在勾选修正时带上）、`mapping_revs` 0 与 1（`source_file`/`overrides` 按新 id 重写，
   指向未复制来源的被丢弃并记 `warnings: mappings_source_refs_dropped`）。
5. `finish(done, result={project_id, template_id, sources, mappings, warnings, manifest_digest})`，通知申请人与决定者 `copy.done`；异常 → `failed` + `copy.failed`。

复制出的项目此后与原项目无关：目标空间的成员按目标空间的角色使用它，它的任务/产物在目标租户里生成；原项目的 `rev`、`mapping_rev`、模板与来源都不变。
副本的项目详情带 `copied_from`（申请 id、源项目名、申请人、批准人、时间、是否自批、是否含来源；不含源空间 id），Docs 页面顶部据此显示来源一行并链接到组织页的审批记录（CI-29）。

## 5. 验收

| 方向 | 证明（接口测试 / 浏览器） |
|---|---|
| 审批完整性 | 目标 viewer 申请 403；非组织成员看审批 404；持 `org.approvals.decide` 但只是目标空间 viewer 的组织管理员：能看、`can_decide=false`、决定 403；未重认证 `reauth_required`；理由 2 字 `reason_required`；同一申请二次决定 `not_pending`；撤回后决定 `not_pending` |
| 审批隐私 | 审批人拿源项目 id 在目标空间 404；清单只有文件名/大小/哈希/扫描状态 |
| 复制完整性 | 副本在目标空间可打开、来源「可用」、预览第一行为「张三」、生成 2 个 DOCX、下载 ZIP 读回两个姓名（浏览器，两引擎）；接口测试同样跑真实任务读回产物文本 |
| 原件不变 | 复制前后 `rev`/`mapping_rev`/`template_id`/名称一致；源项目无任务；副本对个人空间 404 |
| 时效与变更 | 批准后改样本规则再执行 → `stale: source_changed`，目标空间没有副本、申请人收到 `copy.failed`；改名不改摘要 |
| 自行批准 | 目标空间 owner 的个人项目：`can_self_approve=true`，申请即批准、`self_approved=true`、执行后 `done`；组织空间项目作为来源 409 |
| 两个执行者 | 一个执行者列出后另一个先执行完 / 执行中后到 / 列出后撤回：后到者原样返回（`done`/`running`/`withdrawn`），无 `copy.failed`、无 `stale` 卡片，只有一份副本；来源真的改了的申请仍只标一次 `stale`（`source_changed`）并通知一次（`test_a_runner_that_reaches_a_request_someone_else_took_leaves_it_alone`） |
| 移动端 | 审批区在 390 px（触摸）上：清单展开、批准、理由与口令、结果可读（浏览器截图） |

## 6. 明确未做

* 反向（组织 → 个人）与组织间复制：没有需求，且会绕过组织的数据保留边界。
* 复制任务/产物/Webhook/API 凭据：这些是当时那个空间的执行记录与授权，不是项目定义。
* 审批人在批准前修改清单（例如只要部分来源）：申请人按需要撤回重申。
* 批准邮件里的一键批准链接：决定是敏感操作，必须在会话内重认证。
