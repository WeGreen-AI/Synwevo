# 组织内消息：私聊与群聊（CI-26）

同一组织的成员之间的一对一私聊与命名群聊，带回复、表情回应、编辑/删除、附件、提及、置顶/静音/通知模式、搜索，以及跨设备同步的本人阅读位置。
入口在账户源 `/messages`，平台栏（门户与 Docs）有「消息」链接与未读徽标。

## 1. 决策记录

| 项目 | 结论 |
|---|---|
| 问题与基线 | 平台没有任何成员间沟通能力；协作只能走邮件或平台外工具，项目相关的沟通与通知系统脱节。 |
| 成熟参考 | Slack Conversations API（会话类型、成员、游标分页；2026-09-26 读取）：**会话是对象、成员是显式关系、历史按游标翻页**。Slack 通知设置：私聊默认全部、频道默认提及。OWASP WebSocket Security Cheat Sheet（2026-09-26 读取）：握手不是授权，逐条动作鉴权，来源校验，限速。 |
| 对比与选择 | 传输：(a) WebSocket——网关缺 `Upgrade` 透传且多一套来源校验；(b) 纯轮询——最简单但延迟 5 s 且成本随标签页数线性增长；(c) **服务端事件流（SSE）**：同源 Cookie、只读、经现有 nginx（`proxy_buffering off`、300 s 读超时）与 Cloudflare 直接可用，客户端自带重连。选 (c)，并把 (b) 作为**经验证的降级**：流不可用时页面轮询并如实显示「轮询中」。事件流是异步生成器，一个标签页只占一个队列与协程，不占线程池；单进程内存扇出（部署每角色一个 worker，扩到多 worker 需共享总线，已记录为条件）。持久化与权限在 HTTP 与数据库，流只是提示；每次（重）连接都从 history API 按最后序号补取。 |
| 预期变化 | 组织成员可以在账户源发起私聊/群聊；消息实时到达开着的标签页；未读徽标出现在三个外壳的平台栏；私聊/提及产生收件箱项（按会话通知模式与静音）。 |
| 边界与风险 | 权限：只能与同组织活跃成员对话（目录只返回姓名）；会话存在与否对非成员一律 404；组织移除即刻关闭该组织全部会话；流每 25 s 心跳时复查会话与账户状态。历史：新成员只见加入之后（`joined_seq`），退出/移除后什么都看不到，重新加入不恢复。数据：删除只清空正文并标记（序号与回复关系保留）；附件走同一接纳链（clamd），按内容哈希存共享 blob，只对能读到该消息的成员提供，图片内联须 `nosniff` + `sandbox`。链接不展开（无 SSRF 面），只做净化后的可点击链接。限额：每人每分钟 60 条、每小时 30 个附件、每天 20 个群。不做：在线状态、他人已读、端到端加密、跨组织私信、Docs 链接卡片（记录为下一步）。 |
| 验收 | 接口 4 项（幂等发送、阅读位置、反应/编辑/删除权限、非成员 404、群历史从加入起、移除与组织撤权关门、附件链路与下载门、流事件与会话撤销时结束）；两个独立浏览器会话的真实往来（见 verification.md §16.26）。 |
| 发布与恢复 | schema v12 新增五张表，无迁移。回滚不影响其他功能。多 worker 部署前需先引入共享事件总线；当前 compose 每角色单 worker。 |

## 2. 对象与规则

| 表 | 要点 |
|---|---|
| `conversations` | `organization_id`、`kind` dm/group、`dm_key`（组织+两人，唯一）、`name/description`、`last_seq`、`archived_at` |
| `conversation_members` | `role` owner/admin/member、`joined_seq`（可见历史起点）、`left_at/left_seq`、`last_read_seq`（本人阅读位置）、`muted/pinned/notify`(all/mentions/none) |
| `messages` | 会话内单调 `seq`、`client_id`（发送幂等：同发送者同 client_id 只存一条）、`kind` text/system、`body`、`reply_to`、`attachment_id`、`mentions`、`edited_at`、`deleted_at` |
| `message_reactions` | 固定 8 个表情，每人每表情一条 |
| `message_attachments` | `blob`（内容哈希）、服务端嗅探的 `content_type`、`scan`、`status`、未用附件 1 小时后清理 |

历史与离组规则（页面里也写明）：新成员看到加入之后的消息；退出或被移除后会话从列表消失、历史/搜索/流/附件全部 404；重新加入从新的 `joined_seq` 开始；组织移除一次关闭该组织全部会话。群主不能直接退出，须先交接或归档；归档后只读。删除消息对他人显示「消息已删除」，不声称清除他人已下载副本。

## 3. 实时与可靠性

- `GET /api/auth/messaging/stream`：`text/event-stream`，事件 `hello`（含心跳间隔）、`message`、`message_updated`、`conversation`（added/removed/updated/withdrawn）、`read`（本人其它设备的阅读位置）、`end`（会话失效）；每 25 s `: ping`，同时复查会话未撤销/未过期与账户状态。
- 客户端：`EventSource` 自动重连；`hello` 后按最后序号 `?after=` 补取并刷新列表；连续 3 次错误或 `end` 后转为 5 s 轮询并显示「轮询中」。
- 发送：本地先显示「发送中…」，带 `client_id`；失败可重试（同 id）；服务端确认后替换为存储的消息。
- 阅读位置：在底部且标签页可见时上报最后序号；其它设备经 `read` 事件同步；未读 = `last_seq − max(last_read_seq, joined_seq)`（静音会话不计）。
- 滚动：翻看旧消息时新消息不抢滚动，显示「N 条新消息 ↓」；加载更早保持锚点。
- 草稿：按账号+会话存本地，切换账号时清除他人草稿；输入法组合期间 Enter 不发送；Enter/Shift+Enter 偏好可切换。

## 4. 接口

`/api/auth/messaging/…`：`conversations`、`unread-count`（平台栏徽标，三个源均可读）、`directory`、`dm`、`groups`、`conversations/{id}`（GET/PATCH）、`…/members`（POST/DELETE）、`…/leave`、`…/transfer`、`…/members/{uid}/role`、`…/archive`、`…/preferences`、`…/messages`（GET before/after/limit，POST）、`messages/{id}`（PATCH/DELETE）、`messages/{id}/reactions`、`…/read`、`search`、`…/attachments`（POST multipart）、`attachments/{id}`（?inline=1 仅图片）、`stream`、`capabilities`。

## 4.1 Docs 链接卡片（CI-31）

消息正文里指向本部署 Docs 的项目或任务链接（`{Docs 地址}/p/{pid}`、`…/p/{pid}/jobs/{jid}`）在每个读者的屏幕上单独解析成卡片：
`GET /api/auth/messaging/link-card?url=`（每读者每分钟 600 次）由 `synwevo_core.docs_links.DocsLinkCards` 回答——读者必须是该项目所在
工作空间的活跃成员，否则 404 `no_card`；不存在的项目、别的主机上同样路径的链接、非 Docs 链接都答 404，同一个「无」。同一会话的成员身份
不是打开对方项目的权限：别人分享的个人空间草稿在你的屏幕上只是一段可点的链接。卡片内容只有名称、工作空间名、任务状态与成功/失败份数，
不含文件、映射或模板内容。Docs 地址取自应用注册表里 Docs 进程写入的启动地址，不取账户服务自己的 `SYNWEVO_APP_URL`（那是账户服务
自己的地址——通知里的 Docs 链接曾因此指向 `accounts…/p/<id>`，本批一并修正为同一来源）。前端每个链接只查一次并缓存，每条消息最多 3 张卡。

## 5. 验收记录

见 `docs/quality/verification.md` §16.26。
