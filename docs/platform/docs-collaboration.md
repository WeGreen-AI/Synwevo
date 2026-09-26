# Docs 实时多人协同（CI-45）

9.13「有权成员共同编辑同一份工作，实时知道谁加入、操作位置和具体修改」。本轮交付**文档型模板的实时协同编辑**：
同一模板的多位编辑者同时改同一份文档，看到彼此的姓名与光标，服务端逐条决定谁能改，修改按内容块归属到人，
草稿持久化，保存仍然是显式的版本。

## 1. 决策记录

| 项目 | 结论 |
|---|---|
| 问题与基线 | 文档编辑器（`DocEditor`）是单人的：第二个人打开同一模板看到的是打开时的版本，保存时靠 `base_version` 冲突（CI-27/CI-41 的 409 `stale_template`）二选一——要么放弃自己的修改，要么覆盖别人的。没有「谁在这里」「谁在改哪里」「谁改了什么」；两个人同时整理一份合同模板只能轮流。 |
| 成熟参考 | **Yjs 文档**（`docs.yjs.dev`，2026-09-26 读取）：CRDT 共享类型（`Y.XmlFragment`）+ 同步协议（step 1/step 2/update）+ awareness（**临时状态**，光标与在线名单，不进文档）；FAQ 明确「权限不能在 YDoc 内部实际执行」，权限由服务端负责；`UndoManager.trackedOrigins` 支持只撤销自己的修改。**tiptap Collaboration / CollaborationCaret 文档**（`tiptap.dev`，同日）：必须关闭自带的 UndoRedo；不要再设置 `content`（会在每个客户端重复）；caret 通过 provider 的 awareness 渲染。**pycrdt / pycrdt-websocket**（Jupyter 的 y-crdt Python 绑定，MIT；源码读取同日）：`YRoom` 的服务循环、`handle_sync_message`、awareness 编码；**y-websocket 3.1**：`messageHandlers` 可扩展、44xx 关闭码停止重连并发出 `closed`、30 秒无消息即重连。**y-prosemirror / @tiptap/y-tiptap 源码**：节点→`XmlElement`（去掉 `null` 属性），文本串→`XmlText`（mark 名→属性，值为 mark.attrs），行内非文本节点为兄弟元素——服务端按同一规则写入，浏览器才能原样读出。 |
| 对比与选择 | (a) 服务端 OT（Google Docs 式中心化变换）：需要为 ProseMirror 步骤实现变换与重放，工程量与出错面都大，且没有维护中的 Python 实现；(b) 只做在线头像与「有人在改，请刷新」：不满足「共同编辑」，是假协同；(c) 第三方协同云（tiptap Cloud/Hocuspocus 托管、Liveblocks）：文档内容外传，违反数据边界，且是新付费服务；(d) **本方案**：浏览器用 Yjs（tiptap Collaboration/CollaborationCaret + y-websocket），服务端用 pycrdt 在 Docs 进程内跑房间，**所有权限、身份、持久化、归属在服务端**。选 pycrdt 而不是 pycrdt-websocket：后者的 `YRoom` 不区分发送者，无法按人归属与按角色拒写，且其 YStore 是第二个数据库（违反「唯一数据库」）；自己的房间循环约 300 行，依赖只多一个 MIT 的 Rust 绑定。保留：模板版本、`base_version`、撤销/重做/恢复、机器 API、`authz.py` 路由策略、平台会话门。 |
| 预期变化 | 打开已有文档型模板的编辑器即进入房间：顶栏显示「实时协同中」、在线成员（姓名首字+稳定配色）、「变更记录」；他人光标带姓名标签、选区着色；修改即时出现；「有未保存为版本的修改」提示；保存仍生成版本（房间随之标记已保存并同步版本号，另一个人再保存不会误报 409）；恢复/撤销/重做版本时所有人的编辑内容同步替换并收到提示；撤销只撤销自己的修改；离线或断线期间的修改在重连后同步（重连由客户端自动退避）；房间连不上时退回单人编辑已保存版本并明确告知；被降为查看者的连接转为只读并告知，被移出/会话失效则断开（不再重连）。新建模板仍是单人（保存后进入协同）。 |
| 边界与风险 | **权限**：握手复用平台会话门（Cookie 会话、工作空间、应用资格、路由角色策略 `("GET", "/api/collab/templates/{tid}")=None`），加 `Origin` 必须是本站；查看者可加入、可见、写入被丢弃并被告知；每 30 秒（`SYNWEVO_COLLAB_RECHECK_S`）以 `touch=False` 复核会话、账户、成员资格与应用资格——连接不延长会话；awareness 只接受连接所证明的身份。**数据**：房间状态整体持久化到唯一数据库 `collab_docs`（最后一次修改 1 秒后、最后一人离开时同步写入）；`collab_changes` 保留每房间最近 500 条；模板删除清理两者。**一致性**：草稿与版本分离——生成用的是版本；协作者的保存是快照（不改动实时文档），房间外的保存（机器 API、脚本）替换实时文档；恢复/撤销/重做替换实时文档。**部署**：房间在进程内存中，单进程部署（现有 compose）直接可用；多 worker 需要把 `/api/collab/` 粘性路由到一个进程，本记录明示而非隐藏；nginx 需要 `Upgrade` 头（`infra/production/nginx.conf` 已改，生产主机上的副本由运维同步，见 deployment.md）。**格式**：转换只覆盖编辑器契约允许的节点与 mark（`editor_contract.py`），未知节点在浏览器按 schema 校验时被丢弃与单人编辑一致。**语言/手机**：新增 48 条三语言字符串；手机上成员条只显示首字，光标标签保留。**成本**：每次更新做一次块文本 diff（模板级文档毫秒级），awareness 每 15 秒一帧。 |
| 验收 | `apps/docs/tests/test_collab_pm.py`（全部节点/mark 往返、整数类型、重新播种、块文本）；`apps/docs/tests/test_collab.py`（两编辑者收敛与在线名单、awareness 冒名丢弃、查看者只读且写入被拒并告知、无会话 4401、外站 Origin/无 Origin 4403、他空间 4404、他人空间参数 4403、非文档模板 4400、离开后持久化与再进入、保存→版本 2 且房间已保存、恢复→所有人内容替换、房间外保存→重播种、逐块归属与连续输入合并、在线降级为查看者与移出即时生效、撤销会话即断开且连接不延长会话）；浏览器 `ui_e2e_collab.py`（Chromium + WebKit：两个独立账户在同一组织空间同时编辑，一方手机视口；看到对方姓名与光标，输入即时同步，变更记录按人归属，保存后另一方收到版本提示，播种内容经浏览器读出与保存的 JSON 与原版本一致）。 |
| 发布与恢复 | schema v20 两张表（增量，无迁移）；新依赖 `pycrdt==0.14.6`（MIT，manylinux 与 macOS 轮子）；前端新增 yjs / y-websocket / y-protocols / lib0 / @tiptap/extension-collaboration(-caret) / @tiptap/y-tiptap（均 MIT）。回滚到 v19 代码：两张表闲置，编辑器回到单人模式；无数据风险。 |

## 2. 接口

| 接口 | 权限 | 说明 |
|---|---|---|
| `WS /api/collab/templates/{tid}?workspace=` | 空间成员（查看者只读） | Yjs 同步（0）、awareness（1）、查询 awareness（3）帧照协议；服务端自有帧（100）为 JSON：`hello`（我是谁、只读、版本号、是否有未保存修改、在线成员、复核间隔）、`state`、`head`（版本操作：`reason` save/restore/undo/redo/replace、`by`）、`role`、`refused`、`joined`/`left`、`activity`、`closed`。关闭码：4400 非文档模板、4401 未登录/会话失效、4403 Origin/权限、4404 不存在、4410 已删除。 |
| `GET /api/templates/{tid}/collab/changes?limit=` | 空间成员 | `{items:[{user:{id,name}, part, kind, block_no, node_type, excerpt, count, started_at, updated_at}], dirty, online, changes_rev}` |

模板服务在保存/恢复/撤销/重做/删除后调用 `on_template_change`；路由用 `collab.acting(tid, user_id)` 标明操作者，用于归属与区分「协作者的快照」和「房间外的保存」。

## 3. 明确未做

* 表格型模板编辑器（`SheetEditor`）的实时协同。项目页的在场、按映射合并与实时变更见 CI-46（`docs-project-collab.md`）。
* 变更记录里按字符高亮的差异视图（现为块级 + 摘录）；把变更记录并入版本对话框。
* 多进程部署的房间共享（需要粘性路由或进程间转发）。
* ~~评论的实时推送（仍为 30 秒轮询，CI-40）~~ → CI-50 经项目房间的 `change` 帧实时到达（`docs-project-collab.md` §4）。

## 4. 表格型模板的协同（CI-47）

| 项目 | 结论 |
|---|---|
| 问题与基线 | `SheetEditor` 整份保存（`PUT …/builtin` + `base_version`）：两个人从同一版出发各填不同单元格，后者 409 `stale_template`，只能「仍然保存」覆盖对方或放弃自己的；页面看不到谁在同一张表上。 |
| 成熟参考 | **Microsoft「Collaborate on Excel workbooks at the same time with co-authoring」**（2026-09-26 读取）：他人选区以不同颜色显示并可跳转到其位置；同一单元格「最后保存的更改获胜」，官方建议按区域/工作表分工。**Figma 多人同步**（CI-46 已引）：结构化对象按属性做服务端权威的最后写入。 |
| 对比与选择 | (a) 把表格放进共享 CRDT 文档：单元格是独立键，CRDT 可行，但要重写 `SheetEditor` 的状态与撤销栈，且字段/合并区域有业务不变量；(b) 只做在场：不满足 9.13；(c) **本方案**：写入仍是版本，服务端在 `base_version` 落后时按**单元格 / 字段 / 工作表布局 / 工作表**三方合并（`builtin_service.merge_sheets`），只有同一对象双方都改才 409 并列出，`force_ids` 保留自己的；模板房间对表格型模板改为**只承载在场与版本帧**（无共享文档、不持久化）；只对浏览器编辑器开启合并，机器 API 保持整份 409 契约。相较 Excel 的「最后保存获胜」，这里同一单元格不静默覆盖而是让人选。 |
| 预期变化 | 表格编辑器顶栏显示「实时更新中」与在线成员（提示「X 正在看 结果 的 B2」，点击跳到该单元格）；他人保存后：本页无未保存修改则自动载入新版本并提示，有则提示「保存时按单元格合并」；从旧版保存自动合并（提示「已保存并与第 N 版合并」；内容与已保存相同时不新增版本）；真冲突弹出对话框列出单元格/字段/布局，可选以我的为准。 |
| 边界与风险 | 合并只在浏览器路由（`merge=True`），SDK/CLI 不变；布局（行列尺寸、合并区域）作为一个整体判断；`force_ids` 形如 `cell:结果!C1` / `field:<id>` / `layout:<sheet>` / `sheet:<name>`；表格房间不写 `collab_docs`/`collab_changes`；其余与 §1 相同。 |
| 验收 | `test_sheet_collab.py`（合并算法 2 项、HTTP 两人合并/冲突/强制/无变化不加版本/无效基线与机器契约、表格房间在场与版本帧且无持久化）；`test_collab.py` 相应调整；浏览器 `ui_e2e_sheet_collab.py`（Chromium + WebKit，两账户，编辑者手机）。 |
| 发布与恢复 | 无 schema 变化；回滚后表格保存回到整份 409。 |

## 5. 版本操作不能无声丢掉别人未保存的实时修改（CI-49）

| 项目 | 结论 |
|---|---|
| 问题与基线 | 恢复版本 / 撤销 / 重做把实时文档整份替换（§1「预期变化」）。CI-45 起若房间里有人正在输入、还没保存为版本，项目页的「版本 → 恢复此版本」或一次 ⌘Z 就把这些输入换掉，只有事后一条「X 已恢复模板版本」提示——9.13 C.3 明文要求「恢复历史版本…不能覆盖其他人未同步内容而不提示」，机器 API 同样如此。 |
| 成熟参考 | Google 文档「版本历史 → 恢复此版本」（2026-09-26 读取）：恢复是显式确认的新操作，当前内容仍留在历史里；Yjs FAQ：权限与业务规则在服务端执行，CRDT 只负责收敛。 |
| 对比与选择 | (a) 恢复前自动把实时草稿保存为一个版本：把别人半句话变成正式版本并污染历史，且撤销栈随之错位；(b) 仅前端提示：机器 API 与旧页面绕过；(c) **本方案**：服务端在恢复/撤销/重做前问房间（`CollabHub.live_state`：房间打开时读 `room.dirty` 与在线可写成员，无人时读持久草稿 `collab_docs.dirty` 与最后一条变更记录的作者），有未保存实时修改即 409 `live_edits`（`detail.online` / `detail.last_by` / `detail.action`），除非请求显式带 `discard_live: true`。项目页的版本对话框打开时读取同一状态，先把「谁在改、恢复会丢弃」写在确认按钮旁，确认按钮改为红色「确认恢复并丢弃实时修改」；对话框打开后有人才开始输入的竞争由 409 兜底——对话框改写提示并保持在确认态。快捷撤销/重做**从不**丢弃实时修改：只报「这一步没有执行」并指向版本历史。 |
| 预期变化 | 浏览器：`POST …/restore|undo|redo` 可带 `discard_live`；机器 API `POST /api/v1/templates/{tid}/restore` 同样（OpenAPI、SDK `restore_template_version(discard_live=)`、CLI `--discard-live`），未带即 409 `live_edits`。表格型模板房间不可写、永不 dirty，不受影响。手机：提示栈最多显示最新两条（`packages/ui` 样式），不再叠满顶栏。 |
| 边界与风险 | 房间状态读取失败（不应发生）记日志后放行，不让版本操作卡死；`dirty` 是「有实时修改未保存为版本」的房间标记，与 `base_head` 的陈旧检查互不替代（两者都查）。 |
| 验收 | `test_collab.py`：房间 dirty 时恢复/撤销/重做均 409 且点名在线者，`discard_live` 后恢复成 v3 且双方文档同步替换、随后干净状态下撤销无需标志；最后一人带着未保存修改离开后，恢复仍被拒并给出「最后由谁修改」。浏览器 `ui_e2e_restore_live.py`（Chromium + WebKit，两账户，编辑者手机）5 个里程碑。 |
| 发布与恢复 | 无 schema 变化；回滚即回到不询问的旧行为。 |

## 6. 服务重启不丢正在编辑的内容（CI-51，9.16「弱网和多实例」）

| 项目 | 结论 |
|---|---|
| 问题与基线 | CI-45 的房间只在最后一次修改 1 秒后与最后一人离开时写库；进程被优雅关闭时（部署、`platform.py restart`）没有任何钩子把仍在编辑的文档写出去，最后一秒内的修改可能随进程消失；也没有证据说明客户端在服务重启后能带着离线输入重新汇合。 |
| 成熟参考 | Yjs 同步协议（step 1/step 2 以状态向量交换缺失部分，2026-09-26 读取）：重连时客户端把本地已有而服务端没有的更新一并送达；y-websocket 断线后指数退避重连（本项目 `maxBackoffTime` 5 秒）。 |
| 对比与选择 | (a) 每次更新同步写库：模板级文档毫秒级，但把磁盘写放进每个按键的路径；(b) **本方案**：CI-48 的 lifespan 退出钩子调用 `CollabHub.shutdown()`，对每个仍有人的可写房间同步 `persist_now()`；新进程从 `collab_docs` 播种房间（head 与 dirty 一并恢复）；客户端不用改——y-websocket 重连、Yjs 同步步骤把离线输入送到新进程并广播给其他人。 |
| 验收 | `test_collab.py`：两人在房间里输入 → 运行退出钩子 → 编辑者离线继续输入 → 同一数据目录起第二个应用 → owner 连上看到重启前的内容且 dirty/head 正确 → 编辑者带着本地文档重连，owner 收到离线输入，双方一致 → 第二个进程退出时库里是两者之并。浏览器 `ui_e2e_restart_live.py`（Chromium + WebKit，两账户，编辑者手机）：`platform.py restart docs`（SIGTERM → 优雅关闭）期间编辑者继续输入，双方顶栏回到「实时协同中」，owner 看到断线中输入的文字，之后保存的 v2 含全部内容。 |
| 边界与风险 | 覆盖的是单进程、同一数据目录的重启（生产 compose 正是如此）；多实例仍需粘性路由（§3）。SIGKILL（非优雅）时最后 1 秒内的修改仍可能丢——客户端保留本地文档，重连后补上；只有所有客户端也同时关闭才真正丢失。 |

## 7. 命名检查点：保存时给版本起名（CI-52，9.13 D）

| 项目 | 结论 |
|---|---|
| 问题与基线 | 每次保存都记为「在编辑器中保存」，版本历史里一串同样的说明，找不到「法务已审的那一版」；机器 API 也没有字段表达这次保存的含义。 |
| 成熟参考 | Google 文档「为当前版本命名」（版本历史里可命名的检查点，2026-09-26 读取）；Git 的提交说明。 |
| 对比与选择 | 版本表已有 `note`（恢复写「恢复自 vN」、合并写「已与第 N 版合并」），只缺一个入口：编辑器保存体（浏览器与机器 API 同一契约）接受可选 `note`（≤200 字，折叠空白，纯文本），空即默认说明，合并后缀照旧追加；文档与表格编辑器的保存按钮旁各一个「版本说明（可选）」输入框，保存成功即清空；SDK `note=`、CLI `--note`。不做单独的「命名」对话框，也不改历史里已有版本的说明。 |
| 验收 | `test_api_builtin.py` 新增：带说明保存 → 历史显示折叠后的说明，默认说明不变，超长与非字符串 400 `invalid_field`；OpenAPI 与契约三语言同步。浏览器 `ui_e2e_version_note.py`（Chromium + WebKit）：编辑器输入说明并保存 → v2，字段清空；项目页版本历史显示该名；不带说明保存为默认说明；390 px 手机顶栏含该字段不横向滚动。 |
| 发布与恢复 | 无 schema 变化（`template_versions.note` 已存在）。 |

