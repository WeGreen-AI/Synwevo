# 回收不再被引用的文件字节（设计，分批实现）

用户文件的字节按内容摘要存放在 `<data>/blobs/<h[:2]>/<h>`（`synwevo_core.store.Store.put_blob`），
许多表的行引用它们。删除项目、来源、模板、任务时只删除或标记行，字节从不离开磁盘：删除的内容无限期留存，
这是隐私与存储生命周期上的缺口（持续迭代 §8.1「产物、历史版本、预览、临时文件与缓存遵守相同权限和生命周期」；
§8.7）。这份设计由独立规划代理读代码起草（2026-09-28），这里是决定下来的版本。**删除模式尚不存在**；
回收器是否在生产运行、保留多久，都由持有人决定（§3.4）。

## 0. 结论

1. **按旧的 `apps/docs/scripts/blob_audit.py` 写回收器，第一次运行就会删掉在用的数据。** 它漏了与 Docs
   共用同一 blob 存储的 `message_attachments.blob`（Synwevo Message 附件）、`uploads.blob`（已完成、尚未使用的
   断点上传，最长 24 小时里只有它引用）、`organization_templates` / `organization_template_releases` 的
   `working_blob` 与 `info.$.image.original_blob`（组织发布按设计比源模板活得久），以及回归用例文件里的图片原图。
   `authz._REACHABLE` 只描述 Docs 那几条按 blob id 读取的路由，交叉核对抓不到它们。**已补齐（CI-106）**，
   并增加与 `synwevo_core.quarantine.REFERENCING_TABLES` 的第二道核对。
2. **按 mtime 判断宽限期在今天是错的**：`put_blob` 遇到已存在的文件直接返回、不改 mtime；断点上传的
   `_copy_completed_blob` 绕过 `put_blob`，同样不改。写入方协议（§2.4）必须先落地，才谈得上删除。
3. **单靠回收器只能回收「真孤儿」**（撤回的消息附件、改选项后旧的解析产物、写入失败的残留）。要让删除的项目、
   模板、账户的字节真正离开磁盘，先要有行级保留策略把行删掉——那些都是产品决定（§4）。部署前的整目录备份
   `/opt/synwevo/backups/*` 从不清理，会抵消任何删除承诺（D5）。
4. 顺带发现、单独立项：已软删除项目的任务与产物仍能按 id 读取、重跑（`JobService.get` 只校验租户，`_REACHABLE`
   的产物子句不看 `projects.deleted`）；`Messaging.sweep_unused_attachments` 没有调用方；
   `rescan.publication_blobs` 漏了历史发布 `info` 里的图片原图。

## 1. 标记集合

**规则**：行存在就算引用，不论 `deleted` / `status`（含 `rejected`、`expired`、`consumed`、`deprecated`）/
`deleted_at`。回收器从不判断「这一行还算不算数」；让行消失是保留策略的事（§4）。`authz` 的可读集合只是下界：
用它当标记集合会删掉软删除的内容与可重跑任务的输入；它用作「候选里一个都不许出现」的反向检查（§2.2）。

| 位置 | 写入方 | 读取方 | 清点 |
|---|---|---|---|
| `source_files.blob` / `parsed_blob` / `working_blob` | `App.add_source`、`_parse_source`、`copies` | 下载、解析、扫描、`_doc_from_row` | 原有 |
| `templates.original_blob`；`templates.info.$.image.original_blob` | 上传、内置模板、复制、`OrgTemplates.use`、`copies` | 模板下载、填充 | 原有 |
| `template_versions.working_blob` | `new_version`、`restore_version`、字段导入折叠 | 编辑、填充、任务快照 | 原有 |
| `artifacts.blob` | 任务发布 | 产物下载、ZIP、v1 | 原有 |
| `blob_grants.blob` | 预览、排版、脱敏 | `/api/preview-files`、`/api/desensitized` | 原有（唯一引用） |
| `jobs.snapshot`（整篇遍历） | 建任务时从活行复制 | 执行与重跑 | 原有 |
| `message_attachments.blob` | `Messaging.attach`、`forward` | 附件下载、转发、消息补扫 | **CI-106 补** |
| `uploads.blob` | 上传完成 | `use_upload` | **CI-106 补** |
| `organization_templates.working_blob` 与 `info` | `OrgTemplates.publish` / `update` | `OrgTemplates.use`、补扫 | **CI-106 补** |
| `organization_template_releases.working_blob` 与 `info` | 同上 | 补扫 | **CI-106 补** |
| 回归用例 `regression/<ws>/<case>/case.json` | `regression.capture` | （今天回放不取像素，保守标记） | **CI-106 补** |

**不是引用的形似列**（`blob_audit.LOOKALIKES`）：各类令牌/密钥哈希、`uploads.sha256`（客户端声明）、
`member_imports.sha256`（文件不在 blob 存储）、`project_copies.digest`。**存字节本身的列**（`INLINE_BYTES`）：
`users.avatar_blob`、`organization_avatars.image`、`conversation_avatars.image`、`collab_docs.state`——把它们当摘要
交给 `blob_path()` 会出错。**不在 blob 存储的用户字节**各有自己的生命周期：头像（内联）、成员导入原文件（CI-96）、
隔离区、`tmp/`、`mail/`。`blobs/` 里的非 blob 文件（`put_blob` 崩溃留下的临时文件）今天永不清理，清点单列报告。

**在途引用**（宽限期存在的理由）：`put_blob` 之后、插行之前；图片模板上传的转换（最长约 180 秒）；
长事务内写入、提交前不可见；上传完成到 `UPDATE` 之间；**内容去重命中**——`put_blob` 什么都不写、要用它的
主张只在线程内存里（实测 67.8% 的引用走这条路径）；从现有行复制 id 再插入（`copies`、`OrgTemplates.use`、
`forward`、`duplicate`）；进程内预览缓存与浏览器持有的预览 id。

**让标记集合保持正确的结构性守卫**（B2）：core 里的单一登记表（只放数据）；测试比对各读取闸门与现有清单都是它的
子集；模式分类测试（新表或新列未分类即失败）；写入方登记测试（AST 扫描 `put_blob` 等调用处，未登记即失败）。

## 2. 清扫协议（删除模式存在之前必须全部具备）

* **时间闸**：文件 mtime 早于 `G_write`（缺省 24 小时，下限 1 小时），且连续两次独立扫描都判定无引用、间隔至少
  `G_ledger`（缺省 24 小时）；移入暂存后保留 `R_hold`（缺省 30 天，测试实例外不低于 7 天）才可清除。
* **前置条件**（任一不满足即不读不写，退出码 3）：authz 表级核对为 `[]`（无法导入视为未核对，拒绝）；登记表覆盖
  其余读取闸门；库的模式版本不高于代码且没有未分类的表；没有已缺失的数据；拿到 `rescan.run_lock` 且没有备份或
  保留任务在运行；暂存目录与 `blobs/` 同一文件系统；**逐候选反向探测**——对每个租户调用 `authz.blob_readable`，
  并在附件、上传、组织模板表里按摘要各查一次，任一命中即中止整轮。
* **一轮移动**：列出磁盘文件 → 在一个读事务快照里标记（直接列、JSON 整篇遍历、文件根，外加全库兜底扫描只报告）→
  候选 = 磁盘 − 已标记 − 兜底钉住 − `quarantine.standing()` − 过新 → 更新账本 → 分批（每批不超过 200 个、持锁不超过
  1 秒）：`BEGIN IMMEDIATE` → blob 写锁排他 → 逐个复核 → 清单写意图 → `rename` 到暂存并 fsync → 清单记已移动 →
  释放锁、提交。崩溃后文件要么还在 `blobs/`，要么在暂存且清单有记录。
* **写入方协议**（B3，先于任何删除）：锁文件 `<data>/.blob-writers.lock`（不放进 `blobs/`），每次调用新开 fd
  （flock 按打开的描述符生效）；`put_blob`、`put_json_blob`、`_copy_completed_blob`、`quarantine.put_back` 取共享锁，
  命中已有文件时**刷新 mtime**；上传闸门的直接删除与隔离的移除取排他锁；加锁顺序先 SQLite 写锁、再 flock。
  复制类写入方改为在写事务内读源行。
* **暂存、恢复、清除**：清单只记摘要与数字；恢复先核对内容哈希、拒绝放回 `standing` 的摘要；清除前完整标记一次，
  被重新引用的摘要改为恢复并报警；没有 `--yes` 不删除。内容缺失时读取路径打印 `blob.missing sha256=… object=…`。
* **隔离区（CI-90）**：不进入 `quarantine/`；`standing` 的摘要不当候选；`rejected` 行照样算引用；与补扫共用
  `run_lock`。**备份**：备份取维护锁共享端，与移动互斥；暂存区不进归档；恢复重置账本。

## 3. 运行方式与持有人决定

* **CLI 优先**：引擎在 core（`synwevo_core/blob_reclaim.py`，不导入应用），命令由 Docs CLI 暴露（能导入 `authz`
  做核对）：`blobs preview`（只读，按类计数与字节）、`blobs reclaim [--apply]`（首次 `--apply` 只写账本）、
  `blobs held list|restore|purge --yes`。退出码与补扫一致：0 正常、1 有发现、2 用法或锁、3 前置条件不满足。
* **定时模式缺省关闭**（仿 CI-96）：`SYNWEVO_BLOB_RECLAIM=off|preview|on`，清除单独一个开关
  `SYNWEVO_BLOB_RECLAIM_PURGE=off|on`；前置条件不满足时 `/readyz` 报 `blob_reclaim: blocked`（不返回 503）；
  隐私页的「本部署的当前状态」三语言写明开关状态。
* **日志与审计绝不含内容**：审计事件只有批次 id、数量、字节数；摘要只在 0600 的清单与操作员 `--list` 输出里。

| 编号 | 持有人决定 |
|---|---|
| D1 | 在生产运行 `preview`（只读）；结果交持有人看过才进入 D2 |
| D2 | 开启移入暂存（首轮会移走历史孤儿；可恢复） |
| D3 | `R_hold` 天数；清除自动还是手动 `--yes`（清除不可逆） |
| D4 | §4 每一项行保留策略，逐项决定 |
| D5 | 部署前备份 `/opt/synwevo/backups` 的保留期与隐私页措辞 |
| D6 | SQLite `secure_delete` 或定期 `VACUUM`（已删除行的内容留在空闲页与 WAL 里） |
| D7 | 部署备份是否排除暂存区（需改 `receive.py`） |

## 4. 让引用消失的保留策略（今天与缺口）

| 对象 | 今天 | 缺什么 | 性质 |
|---|---|---|---|
| 断点上传 | 24 小时后标 `expired`，行永不删除 | 结束 N 天后删行（`consumed` 至少保留到幂等期） | 技术性 |
| 未发送的消息附件 | 写了 `expires_at = +1h`，清扫**没有调用方** | 接进消息服务的定时清扫 | 技术性，低风险 |
| 撤回、移除的消息附件 | 行被硬删 → 字节成孤儿 | 靠回收器 | 已具备 |
| 预览、排版、脱敏授权 | 不过期；缓存命中不刷新时间 | 过期（如 7 天）并在重新授权时刷新 | 技术性，持有人确认天数 |
| 模板版本 | 无上限 | 保留规则（head、检查点、被固定的、被可重跑任务引用的、已发布的），修剪时重接父链 | 产品决定 |
| 任务快照与产物 | 长期保留；`deleted_at` 从未写入 | 产物保留期；任务可重跑窗口，过期后快照裁成元数据 | 产品决定 |
| 已删除项目 / 来源 / 模板 | 软删除，关联数据全留，任务仍可读可重跑 | N 天后清除；先修正任务可达性 | 产品决定 |
| 账户删除 | 个人工作空间的 Docs 内容保留 | 到期后清除 | 产品决定（隐私上最大的一项） |
| 组织删除（过恢复期） | 工作空间数据保留 | 清除 | 产品决定 |
| 回归用例 | 逐字节副本，永久保留 | 跟随项目或设到期 | 产品决定 |
| 部署前备份 | 永不清理 | 保留 N 份或 N 天 | 持有人与基础设施 |
| 成员导入 | CI-96 已实现，缺省关闭 | 持有人决定是否开启 | 已有先例 |

## 5. 删除模式之前必须证明的

静态完整性（登记表 ⊇ 各读取闸门、模式分类、写入方登记、清点脚本不含删除代码）；场景判定（每个写入方在隔离实例
里跑一遍后，候选集合恰好等于有意制造的孤儿；内容唯一的消息附件不是候选；硬删模板后组织发布仍被标记；硬删来源后
快照仍被标记且能重跑）；并发（去重命中后暂停的 `put_blob` 与一轮移动交错时文件不被移走；跨进程与同进程多线程；
与补扫、备份互斥；各处杀进程后状态一致）；时间闸；隔离区；备份与恢复；恢复与清除；隐私（输出无文件名与用户文本）；
在生产备份的隔离副本上只读演练并交人工复核。

## 6. 分批

1. **B1（只读，CI-106）**：补齐清点的标记集合（附件、上传、组织发布、回归用例文件）；被拒附件与被判感染的组织
   发布按「已撤回」而不是「缺失」；与隔离模块引用表交叉核对；报告写入中断的临时文件、放错目录的 blob、内联字节列；
   修正过时说法（`progress.md`、`operations.md` 的清理说明、`projects prune --delete` 的措辞）。注意
   `verify-restore` 因此变严：合并前先在一份生产备份的副本上跑一遍（需持有人授权访问生产备份）。
2. **B2（只读）**：登记表迁入 core，各处现有清单受测试约束；修正 `publication_blobs`；为新增引用列加索引。
3. **B3（行为变化，不删除）**：写入方协议；复制类写入方在事务内读源行。
4. **B4**：`blobs preview`、逐候选反向探测、账本；生产副本演练 → D1。
5. **B5**：暂存、恢复、清除 CLI（缺省手动）；§5 的并发、崩溃、备份测试。
6. **B6**：定时模式（两个开关缺省关闭）；隐私页三语言状态 → D2、D3。
7. **B7 起**：§4 的保留策略，每项单独一批、各自需要持有人决定。建议顺序：未发送附件 → 上传行 → 授权过期 →
   任务与产物保留（连同已删除项目的任务可达性修正）→ 已删除项目、来源、模板 → 模板版本修剪 → 账户、组织删除后的
   清除 → 回归用例 → 部署备份保留 → `secure_delete` / `VACUUM`。
