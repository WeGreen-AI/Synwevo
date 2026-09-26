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

* 表格型模板编辑器（`SheetEditor`）与项目页字段/映射的实时协同；项目页的「谁在这里」。
* 变更记录里按字符高亮的差异视图（现为块级 + 摘录）；把变更记录并入版本对话框。
* 多进程部署的房间共享（需要粘性路由或进程间转发）。
* 评论的实时推送（仍为 30 秒轮询，CI-40）。
