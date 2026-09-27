# 组织内消息：私聊与群聊（CI-26；独立应用 Synwevo Message，CI-66）

同一组织的成员之间的一对一私聊与命名群聊，带回复、表情回应、编辑/删除、附件、提及、置顶/静音/通知模式、搜索，以及跨设备同步的本人阅读位置。
自 CI-66 起是平台上的独立应用 **Synwevo Message**，有自己的源站：生产 `https://message.wegreen.ltd`（明确例外，不改成 message.synwevo.com），
本机 `http://message.localhost:8768`；会话列表在 `/`，会话在 `/c/{cid}`。平台栏（门户、Docs、账户页）的「消息」链接与未读徽标指向它（§6）。

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

- `GET /api/messaging/stream`（CI-66 前为 `/api/auth/messaging/stream`）：`text/event-stream`，事件 `hello`（含心跳间隔）、`message`、`message_updated`、`conversation`（added/removed/updated/withdrawn）、`read`（本人其它设备的阅读位置）、`end`（会话失效）；每 25 s `: ping`，同时复查会话未撤销/未过期与账户状态。
- 客户端：`EventSource` 自动重连；`hello` 后按最后序号 `?after=` 补取并刷新列表；连续 3 次错误或 `end` 后转为 5 s 轮询并显示「轮询中」。
- 发送：本地先显示「发送中…」，带 `client_id`；失败可重试（同 id）；服务端确认后替换为存储的消息。
- 阅读位置：在底部且标签页可见时上报最后序号；其它设备经 `read` 事件同步；未读 = `last_seq − max(last_read_seq, joined_seq)`（静音会话不计）。
  读到会话末尾时，服务端把该读者关于这个会话的收件箱条目（`conversation.dm/mention/group`，`object_id` = 会话）标为已读——只是收件箱的已读，不批准、不完成任何事（CI-66 修复：此前铃铛在会话读完后仍显示「X 给你发了私信」，只靠寄出前复核避免寄邮件）。
- 滚动：翻看旧消息时新消息不抢滚动，显示「N 条新消息 ↓」；加载更早保持锚点。
- 草稿：按账号+会话存本地，切换账号时清除他人草稿；输入法组合期间 Enter 不发送；Enter/Shift+Enter 偏好可切换。

## 4. 接口

`/api/messaging/…`（消息应用源站；CI-66 前是账户源的 `/api/auth/messaging/…`）：`conversations`、`directory`、`dm`、`groups`、`conversations/{id}`（GET/PATCH）、`…/members`（POST/DELETE）、`…/leave`、`…/transfer`、`…/members/{uid}/role`、`…/archive`、`…/preferences`、`…/messages`（GET before/after/limit，POST）、`messages/{id}`（PATCH/DELETE）、`messages/{id}/reactions`、`…/read`、`search`、`…/attachments`（POST multipart）、`attachments/{id}`（?inline=1 仅图片）、`stream`、`capabilities`、`link-card`、`…/announcement`、`messages/{id}/report`。
仍在账户源的：`/api/auth/messaging/unread-count`（平台栏徽标；自 CI-64 起由共享会话路由在**每个**源回答，消息源也挂）、`/api/auth/messaging/reports…`（举报处理，§4.2）。
账户源对其它 `/api/auth/messaging/…` 路径：注册表里有 `messages` 应用时答 `410 moved`（`detail.messages_url` 指出新地址）；没有该应用的部署由组装层在 `accounts` 角色上挂旧路径兜底（§6）。

## 4.1 Docs 链接卡片（CI-31）

消息正文里指向本部署 Docs 的项目或任务链接（`{Docs 地址}/p/{pid}`、`…/p/{pid}/jobs/{jid}`）在每个读者的屏幕上单独解析成卡片：
`GET /api/messaging/link-card?url=`（每读者每分钟 600 次）由 `synwevo_core.docs_links.DocsLinkCards` 回答——读者必须是该项目所在
工作空间的活跃成员，否则 404 `no_card`；不存在的项目、别的主机上同样路径的链接、非 Docs 链接都答 404，同一个「无」。同一会话的成员身份
不是打开对方项目的权限：别人分享的个人空间草稿在你的屏幕上只是一段可点的链接。卡片内容只有名称、工作空间名、任务状态与成功/失败份数，
不含文件、映射或模板内容。Docs 地址取自应用注册表里 Docs 进程写入的启动地址，不取账户服务自己的 `SYNWEVO_APP_URL`（那是账户服务
自己的地址——通知里的 Docs 链接曾因此指向 `accounts…/p/<id>`，本批一并修正为同一来源）。前端每个链接只查一次并缓存，每条消息最多 3 张卡。

## 4.2 群公告与举报处理（CI-34）

**决策记录。**

| 项目 | 结论 |
|---|---|
| 问题与基线 | 群里没有任何「置顶信息」：规则、截止时间、文件放哪里只能反复发消息，新成员看不到加入前的消息（§2 的规则），于是同一句话要说很多遍。也没有任何举报通道：骚扰、垃圾信息或索要凭据的消息，成员只能私下告诉管理员，管理员看不到证据，删除也只有群主/群管理员能做，私聊里谁都做不了。 |
| 成熟参考 | Telegram FAQ「Groups and Channels」（2026-09-26 读取）：管理员可把一条消息钉在聊天顶部，所有成员会收到通知（即使静音）。Microsoft 支持「Report a concern in Microsoft Teams (free)」（同日）：任何人可举报私聊/群聊/社区里的消息，选一个最接近的类别并可附说明；提交的是会话线程信息、时间、举报人、类别与说明，**群聊会连带最近十条消息**。Google Workspace 管理员帮助「Take action on reported Chat content」（同日）：持服务设置权限的管理员在审核工具里看被举报消息，可删除该消息、删除整个空间、或「解决」并可附评论，**解决后的状态不可再改**。两处官方页面都没写举报人是否会被告知结果。 |
| 对比与选择 | 公告：(a) 钉任意一条消息（Telegram）——要先发出去再钉，且内容随原消息的编辑/删除变化；(b) **本方案**：群的一段公告文本（≤1000 字），由群主/群管理员在群信息里写与清除，钉在线程顶部、超过两行可展开，写入一条系统行，成员在收件箱得到一条汇总项（清除不通知）。举报：(a) 像 Teams 那样连带最近十条消息——审核者看到的比举报所需的多，与本项目「管理权不授予内容访问」的原则冲突；(b) **本方案**：只快照被举报的那一条（正文、附件名、发送者），举报人与原因/说明，会话只给类型与群名；审核者是持 `org.messaging.manage` 的组织成员（owner/admin 默认，自定义角色可授），处理需要与其它组织写操作相同的**重新验证身份**，结果只有两种：删除（对所有人显示「消息已删除」，等同群内管理员删除）或驳回；同一条消息的其它未处理举报随之处理；举报人在收件箱得到结果与处理说明（官方参考没写这一步，本项目补上：否则举报像石沉大海）。保留：现有群内删除权、消息编辑/删除语义、通知目录与偏好。 |
| 预期变化 | 群信息抽屉（管理者）多出「群公告」文本框与保存/清除；线程顶部出现「公告」条；系统行「X 更新了群公告 / 清除了群公告」。别人的消息工具栏多一个「举报」（⚑）：弹窗先说明管理员会看到什么，选原因（骚扰或辱骂 / 垃圾信息 / 泄露隐私或索要凭据 / 其他）、可写说明，提交后告知「处理后会在通知里看到结果」。组织页多出「举报处理」区（仅持权者可见）：待处理/全部，每条显示群名或「私聊」、举报人、时间、原因、说明、被举报消息的快照（引用块）、处理人与说明；「删除消息」「驳回举报」走 OrganizationAction（原因 ≥8 字 + 重新验证）。收件箱：`conversation.announcement`（汇总）、`conversation.reported`（可操作，链接到组织页 `?report=`）、`conversation.report_removed` / `report_dismissed`（汇总）。 |
| 边界与风险 | 只能举报自己能看到、非自己发的文本消息（加入前的消息与已删除的 404，自己的 400）；同一人对同一条消息只保留一条未处理举报（409）；每人每天 20 条。快照在举报时固定，发送者之后编辑不影响审核。举报列表与处理只对 `org.messaging.manage` 开放（403），处理需 `admin` 范围的新鲜重认证（`reauth_required`），重复处理 409。删除走与群内删除同一条路径（正文清空、表情回应删除、`message_updated` 推给在线成员）。审核者看不到会话其它消息、不能进入该会话；举报人的姓名对审核者可见（官方参考同样如此），对发送者不可见。公告 ≤1000 字、归档群不可改；公告通知走收件箱汇总，不写入推送以外的渠道。三语言 41 条；手机：公告条不横向滚动，举报弹窗与组织页在 390 px 可完成。 |
| 验收 | `services/identity/tests/test_messaging.py::test_announcements_and_reports_go_to_the_right_people_and_a_review_settles_the_message`；浏览器 `ui_e2e_moderation.py`（Chromium + WebKit，举报人在手机）。 |
| 发布与恢复 | schema v17：`conversations` 增三列（公告）、新表 `message_reports`，均为增量；回滚到 v16 代码时公告列被忽略、举报表闲置。 |

**接口。**

| 接口 | 权限 | 说明 |
|---|---|---|
| `PUT /api/messaging/conversations/{cid}/announcement`（消息源） | 群主/群管理员 | `{text: string \| null}`；返回会话（含 `announcement`、`announcement_at`）；写系统行；成员得到 `conversation.announcement` |
| `POST /api/messaging/messages/{mid}/report`（消息源） | 会话成员，非本人消息 | `{reason: harassment\|spam\|privacy\|other, note?}`；201 举报；持 `org.messaging.manage` 者得到 `conversation.reported`（目标是账户源的组织页） |
| `GET /api/auth/messaging/reports?organization_id=&status=open\|removed\|dismissed\|all`（账户源） | `org.messaging.manage` | 举报列表（快照、发送者、举报人、原因、说明、处理结果）与可用原因 |
| `POST /api/auth/messaging/reports/{rid}/review`（账户源） | `org.messaging.manage` + 新鲜重认证 | `{decision: removed\|dismissed, note?}`；同一消息其它未处理举报随之处理；举报人得到结果 |

举报的**处理**留在账户源（`synwevo_identity/messaging_reports_routes.py`）：它要求组织权限与账户会话上的严格重认证，由组织页完成；审核者不进入会话。
代价是事件流按进程扇出：账户源上删除的消息，消息源上开着的标签页在下一次同步（重连或任一会话事件触发的补取）时才显示「消息已删除」。

## 5. 验收记录

见 `docs/quality/verification.md` §16.26（消息）、§16.34（群公告与举报处理）、§16.74（独立源站）。

## 6. 独立源站（CI-66）

**决策记录。**

| 项目 | 结论 |
|---|---|
| 问题与基线 | 消息页寄居在账户源 `/messages`：账户服务是「口令只在一个地方输入」的那个源，却承载了一个业务应用的实时流、附件与草稿；平台栏三处「消息」链接各自从 `account_url` 拼地址；域名迁移（CI-67）已把消息的对外地址定为独立主机 `message.wegreen.ltd`。 |
| 选择 | 第四个应用 `apps/messages/`（`synwevo_messages`，外壳 `@synwevo/messages-web`，角色 `messages`），与门户同构：`build_platform → new_api → install(role="messages") → install_probes（附件扫描器可降级）→ 共享会话路由 → 依赖方路由 → 账户重定向 → 外壳`。业务层与六张表**原样**在 `synwevo_core.messaging`，无迁移、id 不变。HTTP 路由从账户服务搬来，只改前缀 `/api/messaging`；举报处理留在账户服务（§4.2）。 |
| 注册表与资格 | `apps` 行 `messages`（名 Synwevo Message，`launch_url` 由消息进程按 `SYNWEVO_APP_URL` 写入：自己的源是 `/`，`all` 角色是 `<源>/messages`）。会话按组织归属而非工作空间，所以 `Apps.check_access` 对它与平台 id 同规则：任何有效账户可打开，会话内能看到什么由服务层决定（组织成员、会话成员、`joined_seq`）；运维仍可 `app disable messages`。门户卡片与账户页「返回」目标自动出现（`apps_for` 对无工作空间者不再拼 `workspace_id=None`）。 |
| 三个源怎么知道地址 | `/api/auth/state.messages_url`：注册表行的 `launch_url`（无则 `null`）。共享 `messagesHref(state)`：跨源经该源 `/auth/start?next=…&lang=…` 一跳登录；同源（`all`）是纯路径；未注册回退账户源 `/messages`。通知目标新增源名 `messages`（路径 `/c/{cid}`），读取时经注册表解析（同 Docs 的 `docs_base`）；未注册仍写 `accounts` + `/messages/{cid}`。 |
| 单一写入源 | 账户源对旧 `/api/auth/messaging/…`：注册表有该应用时**每次请求**答 `410 moved`（不缓存，注册即生效，两个进程不会各自扇出）；`GET /messages[/{cid}]` 答 `303 <消息源>/auth/start?next=/c/{cid}`（地址只来自注册表，`cid` 必须匹配 `cv_…`，否则落到列表）。没有该应用的部署：组装层（唯一可同时见到两个包的地方）在 `accounts` 角色上挂旧前缀兜底，账户外壳不再有消息页。 注册表**没有**该应用时（未添加，或本次发布到隧道主机名就绪之间的窗口），账户源在 `/messages`、`/messages/{cid}`、`/c/{cid}` 直接提供消息外壳本身（外壳按本源角色把接口前缀改为 `/api/auth/messaging`），会话照常可用；应用注册后同一标签页收到 `410 moved` 时外壳自行跳到新源（CI-66 补充）。 |
| 网关规则 | `ROLES` 加 `messages`：放行 `/api/messaging/`、`/auth/`、`APP_ACCOUNT_PATHS`（含 `unread-count`），拒绝账户面、`/api/portal/`、Docs 业务 API 与旧 `/api/auth/messaging/*`；其它角色对 `/api/messaging/` 的拒绝点名消息源地址。`ACCOUNT_LEVEL_PREFIXES` 加 `/api/messaging/`：`all` 角色上不套 Docs 的工作空间/资格检查。 |
| 浏览器状态 | 草稿（`synwevo.msg.draft.*`）、Enter 偏好、标签页空间上下文按源隔离，旧源上的不迁移；一次性代价。 |
| 发布与回滚 | 无 schema 变化。运维：隧道公开主机名 `message.wegreen.ltd` → 网关；`compose.yaml` 加 `messages` 服务（角色 `messages`，`SYNWEVO_APP_URL/PUBLIC_URL=https://message.wegreen.ltd`，`messages-web`/`messages`，clamd 两项）；`nginx.conf` 加 server 块（`/api/messaging/stream` 长读超时）；`client register --client-id messages-web --app messages --redirect https://message.wegreen.ltd/auth/callback --post-logout …/signed-out --backchannel …/auth/backchannel-logout`；消息进程首次启动写入注册表行。回滚：`app disable messages`（账户源随即恢复旧前缀？——**不**：410 由行的 `status='active'` 决定，停用后旧前缀在 `accounts` 角色上恢复兜底，链接回退账户源 `/messages`，但账户外壳已无该页，需回退镜像才有页面）。 |
| 验证 | `apps/messages/tests`（7 项：自描述与角色拒绝、私聊/阅读位置/收件箱已读、群历史与撤权、附件链、事件流、链接卡片、公告与举报提交）；`tests/integration/test_messages_origin.py`（5 项：旧路径 410/303 切换、四源 `messages_url`、SSO 落点与通知目标、平台级退出、注册表资格）；`test_messaging_mail.py`（私信邮件规则，随路由搬到组装层套件）；`ui_e2e_messaging.py` / `ui_e2e_shell.py` 改为驱动消息源（verification.md §16.74）。 |

## 7. API 契约（第九轮，CI-74）

本节是消息外壳（`apps/messages/web`）与任何客户端消费的**契约**：写在这里的字段与状态码是承诺，没写的不要猜。
既有字段与路由全部保留；本节只增。服务端仍是 `ServiceError(status, code, message[zh-Hans], detail)`，每个错误 `code` 稳定，展示文案由客户端按 code 翻译。

### 7.1 决策记录

| 项目 | 结论 |
|---|---|
| 问题与基线 | §1 的边界写着「不做：在线状态、他人已读」，也没有撤回窗口、转发、语音、标记未读、定时静音、全文索引与断线补发；`DELETE /messages/{mid}` 是发送者「对所有人删除」且不限时，与「撤回」混为一谈；事件流没有 `id:`，断线即全量重拉；`hub.connections()` 无人读取；`Organizations.remove_member` 自建的 `Messaging` 没有扇出钩子，`withdrawn` 事件从未到过任何流。 |
| 成熟参考 | 第九轮简报 §三：WhatsApp（回执互惠、编辑 15 分钟、转发 ≤ 5 会话、语音）、微信/QQ（撤回 2 分钟、逐条转发）、Matrix `m.typing`/`m.presence`/MSC2285（私有已读、typing 服务端无状态）、Zulip（presence 每分钟心跳、双开关回执）、MDN EventSource（`id:`/`Last-Event-ID`/`retry:`）、SQLite FTS5（external content + 触发器、trigram ≥ 3.34、< 3 字不命中）。 |
| 对比与选择 | **在线**：只存内存里每人最近心跳，三态由心跳年龄推导（在线 ≤ 60 s，离开 ≤ 5 min，其余离线），不做「最后在线」，重启即全员离线——服务端不知道的就不显示。**回执**：不发逐条回执行，只暴露既有 `last_read_seq`，且只在读者与被看者都开启时显示；**没有「已送达」**（`MessageHub.publish` 只是进程内入队，证明不了到达）。**撤回 vs 删除**：撤回 = 发送者、对所有人、120 s（微信/QQ 尺度，可配）、留标记；删除 = 只对我（`message_hides`）；群管理员移除 = 不限时、标名；举报处理沿用。**搜索**：FTS5 external-content + 三个触发器（撤回/编辑即更新索引），trigram 可用即用，短于 3 字或 unicode61 遇中文退回 LIKE 并如实标注 `fallback`。**补发**：进程内有界环（5000 条）+ 事件 `id`，环够不着就 `resync`，客户端再走 `GET /sync` 与 history `after=`；typing/presence 不进环。**撤权**：本进程的移出即时推送；别的进程（组织移除在账户服务）造成的成员资格丢失由流心跳复查发现，≤ 1 心跳。**跨进程外发表**（多 worker、Docs 写卡片的前提）不在本批。 |
| 预期变化 | 见 7.2–7.13。`DELETE /messages/{mid}` 语义变化（对我删除）：返回体不再含 `deleted`，旧外壳照旧渲染直到改版（外壳应按 `hidden` 处理）。 |
| 边界与风险 | 每个新面都逐读者鉴权（`_member`/`joined_seq`/`message_hides`）；非成员一律 404；typing/presence 有独立限速桶、不落库、不审计、不补发；在线与回执都是**互惠**开关；进程内状态（在线、typing、环）以「每角色一个 worker」为前提，多 worker 需共享总线。撤回删除附件行（同哈希 blob 共享，转发副本有自己的行）。语音只按文件头判型，不解码音频，`duration_ms` 是客户端声明、服务端校验范围。FTS 按 `messages.rowid` 建索引：本平台不跑 `VACUUM`（备份用在线备份 API），需要时 `messaging_search.rebuild()`。 |
| 验收 | `apps/messages/tests/test_messaging_r9.py`（17 项）、`tests/integration/test_messaging_r9_composed.py`（3 项：寄出前复核、`all` 角色新路由与徽标与计数、schema v24）；隔离实例 httpx 证明（两名经邮件注册与邀请的真实账户、四源、SSO 一跳）。 |
| 发布与回滚 | schema **v24**：只加表（`messaging_settings`、`message_hides`、`messages_fts` 虚表 + 3 触发器）与列（`conversation_members.marked_unread/muted_until/history_from/last_read_at/updated_at`、`messages.recalled_at/removed_by/forwarded_from`、`message_attachments.duration_ms`）；既有 CHECK 不动。**回滚到 v23 代码**：多余表/列被忽略；触发器仍随写入维护索引，再升级不必重建；撤回过的消息在旧代码里读作「已删除」（同时置了 `deleted_at`）；对我删除的行在旧代码里重新可见（旧代码不读 `message_hides`）。彻底清除的 SQL：`DROP TRIGGER messages_fts_ai; DROP TRIGGER messages_fts_ad; DROP TRIGGER messages_fts_au; DROP TABLE messages_fts; DROP TABLE message_hides; DROP TABLE messaging_settings;`（列保留）。回填：FTS 建表时一次 `rebuild`，同步读全表；本轮无其它回填。 |

### 7.2 设置：`GET/PUT /api/messaging/settings`

`{show_presence: bool, read_receipts: bool}`。缺省 `show_presence=true`、`read_receipts=false`（表 `messaging_settings`，无行即缺省）。`PUT` 只接受这两个布尔键（其它键或非布尔 400 `invalid_field`）。两个开关都**互惠**：

* 关掉 `show_presence`：别人看到你的 `presence` 为 `null`，你看到所有人也是 `null`；你的 typing 不再发出，也收不到别人的；服务端立刻忘掉你的心跳，并向此前能看见你的人推一条 `presence {state: null}`。
* 关掉 `read_receipts`：你不出现在任何人的 `read_by` 里，你自己看到的 `read_by` 是 `null`（不是空数组）。

`capabilities.settings` 带当前值，外壳启动时不用多请求一次。

### 7.3 在线（presence）与正在输入（typing）——都不落库

**定义（逐字进隐私页）**：在线 = 过去 60 s 内有一个可见的 Synwevo 页面发过心跳；离开 = 最近一次心跳在 60 s–5 min 之前（页面隐藏后就不再发心跳）；离线 = 其余（含从未上报、进程重启后）。没有「最后在线时间」。
`capabilities.presence = {online_s: 60, away_s: 300, interval_s: 60, states: [online, away, offline]}`。

| 接口 | 说明 |
|---|---|
| `POST /api/messaging/presence` | 心跳。客户端**只在 `document.visibilityState === 'visible'` 时**每 `interval_s` 发一次；每人每分钟 12 次（独立桶）。答 `{shown, state, online_s, away_s, interval_s, states}`；关了 `show_presence` 的人 `shown=false, state=null` 且不被记录。 |
| `GET /api/messaging/presence?user_ids=a,b` | 批量快照（≤ 200 个 id），只回答与调用者**共享至少一个活动会话**的人：`{presence: {uid: online\|away\|offline\|null}, online_s, away_s, interval_s, states}`；不共享的 id 直接不出现。 |
| 会话读取 | `GET /conversations`、`GET /conversations/{cid}`：`peer.presence` 与 `members[].presence` 同一取值；`null` = 不显示（对方或你关闭了开关，或本进程没有心跳登记）。 |
| SSE `presence` | `{type:'presence', user_id, state: online\|away\|offline\|null}`。只在**状态变化**时推：心跳把人从离开/离线变为在线时；流心跳（每 25 s，进程内每 ≥ 10 s 扫一次）发现有人老化为离开/离线时；关闭开关时 `null`。只推给共享会话且双方都开着开关的人。**不进补发环**。 |
| `POST /api/messaging/conversations/{cid}/typing` | `{state: 'start'\|'stop'}`（缺省 start，其它 400）。非成员 404。每人 10 s 内 20 次（独立桶，不计入消息限额，超出 429 + `retry_after`）。答 `{sent, state, expires_in_s: 6}`；`sent=false` 表示没有人可收（你关了 `show_presence`，或对方都关了）。 |
| SSE `typing` | `{type:'typing', conversation_id, user_id, name, state, expires_in_s}`，只发给会话内其它开着 `show_presence` 的活动成员；接收方 `expires_in_s` 秒内没有新的 `start` 或收到 `stop` 即清除。客户端建议：首键发 `start`，每 4 s 重发，停止输入 3 s 或发送后发 `stop`。**不进补发环、不审计、不落库**。 |

### 7.4 已读回执（opt-in）

`GET /conversations/{cid}` 与 `GET /conversations/{cid}/messages` 都带 `read_by`：

* `null`：不显示（你没开 `read_receipts`）。
* `[]` 或列表 `[{user_id, last_read_seq, at}]`：只列**开了回执的其它活动成员**；私聊里对方开了才有一项，群里只统计开了的人（文案应写「已读 N（仅统计开启回执的成员）」）。`at` 是该读者读游标最后移动的时间。

SSE `receipt`：`{type:'receipt', conversation_id, user_id, last_read_seq, at}`——读者开了回执且读游标前移时，推给会话内其它**开了回执**的成员。
一条消息「已读」= 某位开了回执的成员 `last_read_seq ≥ seq`。**没有任何「已送达」字段**，界面不得显示送达。

### 7.5 标记未读与定时静音

| 接口 | 说明 |
|---|---|
| `POST /conversations/{cid}/unread` | 置 `me.marked_unread=true`，**不倒退** `last_read_seq`。答 `{conversation_id, last_read_seq, marked_unread: true, unread}`（`unread` 至少 1）。本人其它设备收到 `read {…, marked_unread: true}`。 |
| `POST /conversations/{cid}/read` | 任何一次读（即使游标不动）清除 `marked_unread`；答里多了 `marked_unread: false`。 |
| 徽标 `/api/auth/messaging/unread-count` | 标记未读且没有未读消息的会话按 **1** 计入 `unread` 与 `conversations`；静音（`muted` 或 `muted_until` 未到期）的会话不计。 |
| `PUT /conversations/{cid}/preferences` | 新增 `muted_until`：未来一年内的 Unix 秒 → 定时静音；`null` → 清除。过去的时间或非数字 400 `invalid_field`。`muted` 是长期静音，两者独立；`me.muted_until` 原样返回。定时静音期间：徽标不计、`recipients_for` 不产生收件箱条目、寄出前复核视为 `conversation muted`。 |

`me` 现在是 `{role, joined_seq, last_read_seq, muted, muted_until, marked_unread, pinned, notify, history_from}`。

### 7.6 撤回、对我删除、管理员移除、编辑窗口

| 接口 | 权限 | 结果 |
|---|---|---|
| `POST /messages/{mid}/recall` | **发送者本人**，发出后 `capabilities.recall_window_s` 秒内（缺省 120，环境变量 `SYNWEVO_MESSAGING_RECALL_WINDOW_S`，0 = 不限时） | 对所有人：`body=''`、附件行删除、表情回应删除、搜索索引清空；行保留 `seq`，`recalled_at` 与 `deleted_at` 同时置上。返回消息体 `{deleted: true, recalled: true, system: {event: 'recalled', actor}}`——外壳在原位渲染「X 撤回了一条消息」。所有成员收到 `message_updated`。超窗 403 `recall_window_closed`（`detail.recall_window_s`）；非本人 403 `forbidden`；已撤回/删除 409 `already_gone`。 |
| `DELETE /messages/{mid}` | 任何能看到该消息的成员 | **只对我删除**：写 `message_hides(message_id, user_id)`；此后本人的 history、search、附件下载、列表预览都看不到它（预览 `last_message.hidden=true`），别人不受影响。答 `{message_id, conversation_id, seq, hidden: true}`；幂等；本人其它设备收到 `message_hidden {conversation_id, message_id, seq, hidden}`。加入前的消息 404。 |
| `POST /messages/{mid}/unhide` | 本人 | 撤销对我删除（外壳给 5 s 撤销）；答 `hidden: false`。 |
| `POST /messages/{mid}/remove` | 群主/群管理员，不限时；私聊 409 `not_a_group`；普通成员 403 | 对所有人删除并**标名**：`{deleted: true, removed_by: {user_id, name}, system: {event: 'removed', actor}}`。附件、表情、索引同撤回。 |
| 举报处理（账户源） | `org.messaging.manage` + 重认证 | 不变：`deleted: true`，无 `recalled`/`removed_by`（显示「消息已删除」）。 |
| `PATCH /messages/{mid}` | 发送者本人，`capabilities.edit_window_s` 秒内（缺省 900，`SYNWEVO_MESSAGING_EDIT_WINDOW_S`） | 超窗 403 `edit_window_closed`；`edited_at` 置上，不重新通知；索引随之更新。 |

`reply` 引用块新增 `recalled: bool`（与 `deleted` 并列）。旧的「发送者对所有人删除」不再存在——它就是撤回。

### 7.7 转发：`POST /messages/{mid}/forward`

请求 `{conversation_ids: [cid…], client_id?}`：1–`capabilities.limits.forward_targets`（5）个目标（多了 400 `too_many_targets`）；`client_id` `[A-Za-z0-9_-]{1,60}`，每个目标实际使用 `{client_id}-{n}`，重试返回已存的消息（`created: false`）。
转发者必须**能读源消息**（成员、`seq ≥ joined_seq`、未撤回/删除、未对我删除，否则 404）且**能写每个目标**（成员、未归档，任何一个不行整批 404/409，什么都不写）。每个目标计一次消息限额。答 `201 {items: [{conversation_id, message, created}]}`。

转发出的消息：`body` 同源、无 `reply`、无 `mentions`、`forwarded_from = {sender: 源发送者姓名, conversation_id: 源会话 id 或 null}`——**只有读者自己也是源会话活动成员时**才给 id，否则 `null`（事件流按接收者分两份载荷）。
附件：只有转发者能读到源附件时才会**新建一行附件**指向同一 blob（内容哈希，不重传），目标读者按目标会话自己的楼层与成员资格读它；对源会话仍然 404。正文里的 Docs 链接不预解析，读者屏幕上照 §4.1 逐读者出卡片。

### 7.8 语音附件

`POST /conversations/{cid}/attachments`（multipart）新增可选表单字段 `duration_ms`。类型**只按字节嗅探**：`audio/webm`（EBML + `webm`）、`audio/ogg`（`OggS`）、`audio/mp4`（`ftyp` 且只有音频轨）、`audio/aac`（ADTS）；客户端声明的 MIME 不采信。
`capabilities.voice = {types, max_bytes: 10 MiB, max_duration_ms: 300000}`：语音超过 10 MiB → 413 `file_too_large`；`duration_ms` 必须是 1–300000 的整数（否则 400），非语音附件声明时长 → 400。
返回与消息里的 `attachment` 对语音多两个字段：`voice: true, duration_ms`（未声明为 `null`——不显示假时长）。语音与图片一样 `inline: true`：`GET /attachments/{aid}?inline=1` 以真实类型、`Content-Disposition: inline`、`nosniff` + `sandbox` CSP 提供，供 `<audio src>` 播放。接纳链（`accept_upload` → 扫描 → 内容哈希）与 `scan.status`（含 `not_configured`）不变。

### 7.9 搜索：`GET /search`

参数：`q`（≥ 2 字）、`organization_id`、`cid`（限定会话）、`from`（发送者 user id）、`has_attachment`（0/1）、`since`/`until`（Unix 秒，按 `created_at`）、`limit`（≤ 100）。
答 `{items, engine: 'fts5'|'like', tokenizer: 'trigram'|'unicode61'|null, fallback: bool}`。每个 `item` 是完整消息体外加 `cid`（= `conversation_id`）、`snippet`（纯文本片段）与 `ranges`（`[[start, end], …]`，`snippet` 内的码点区间，命中处）；客户端按区间高亮，**不要**把 `snippet` 当 HTML。
范围：调用者的活动成员资格、`seq ≥ joined_seq`、`kind='text'`、未撤回/删除、未对我删除。被移出会话即刻搜不到。
引擎：`capabilities.search = {engine, tokenizer, min_chars, filters, max_limit}` 来自 `sqlite_master`（启动时探测，不假设）。trigram 下 `q` 短于 3 字、unicode61 下 `q` 含中日韩字符、或本构建没有 FTS5 → 用既有 LIKE（`fallback=true` 表示索引存在但这次没用上）。用户输入整体作为一个 FTS **短语**（引号加倍），`*`、`NEAR`、括号、中文标点都不会成为操作符或语法错误。索引是 external-content（`content='messages'`），三个触发器只索引 `kind='text'` 行，撤回/编辑/移除立即生效；建表时一次 `rebuild` 回填。

### 7.10 事件流：`id:`、`Last-Event-ID`、`resync`、`GET /sync`

每条事件都有 `id: <整数>` 行且载荷里同样带 `id` 与 `at`（进程唯一、单调递增，以时间戳毫秒为起点，重启后仍递增）。`hello` 也带 `id:`（= 当前最新 id），载荷 `{heartbeat_s, last_event_id, typing_ttl_s, presence: 定义, replay: bool}`。
事件类型：`hello`、`message`、`message_updated`（编辑/撤回/移除/反应）、`message_hidden`（本人其它设备）、`conversation`（added/removed/updated/withdrawn；`detected: 'heartbeat'` 表示由流心跳复查发现）、`read`（本人其它设备，含 `marked_unread`）、`receipt`、`typing`、`presence`、`resync`、`end`（`reason: session | too_many_streams`）。

**补发**：重连时带浏览器自动附上的 `Last-Event-ID` 头，或（新建 `EventSource` 时）查询参数 `?last_event_id=`。服务端从进程内有界环（`capabilities.stream.replay_ring` = 5000 条）取出 id 更大、且发给本人的事件，**逐条重查会话成员资格**（已被移出的会话只补发 `conversation removed` 本身），typing/presence 从不补发。游标比环还老、或来自另一个进程（比当前 id 大）→ 发 `resync {reason: 'cursor_expired'}`。已补发的事件不会再从实时队列重复发（按 id 去重）。
**并发上限**：每人 8 条流；第 9 条立即收到 `end {reason: 'too_many_streams', max: 8}` 并 `retry: 30000`。
**心跳**（25 s）：复查会话与账户状态 → `end {reason: 'session'}`；对比这个标签页开始时可见的会话集合，**任何进程**里关闭的成员资格（组织移除发生在账户服务进程，举报删除亦然）在下一个心跳内以 `conversation removed`（`detected: 'heartbeat'`）告知，同一次丢失只告知一次（跨标签页去重）；随后扫一次在线老化。

`GET /sync?since=<cursor>`（`since` 是上次答里的 `cursor`，首次 0）：

```
{cursor, since, conversations: [{id, kind, last_seq, last_message_at, updated_at, archived, me, unread}], left: [cid…],
 recalled: [{id, conversation_id, seq, how: recalled|removed|deleted}], truncated: bool}
```

`conversations` 是 `since` 之后**会话本身有变化**（新消息、系统行、成员变化）或**本人成员行有变化**（读游标、标记未读、静音、置顶、角色）的活动会话；`left` 是 `since` 之后退出/被移出的会话；`recalled` 是可见范围内 `since` 之后被撤回/移除/删除的消息。**从不带正文**。

**客户端算法**：
1. 打开 `EventSource(/stream)`；记住每条事件的 `id`（浏览器自动记 `lastEventId`），并在每次 `hello`/`sync` 后保存 `cursor`。
2. 断线重连由 EventSource 自动带 `Last-Event-ID`；`hello.replay=true` 之后到来的事件按正常事件处理。
3. 收到 `resync`（或连续失败改为轮询）→ `GET /sync?since=cursor` → 对 `conversations` 里的每个会话按本地已知 `seq` 拉 `messages?after=`；`left` 直接从列表移除；`recalled` 就地改成标记；保存新 `cursor`。
4. 流不可用时 5 s 轮询 `/sync` 也能补齐（`hello` 后不再需要「全量刷新列表」）。

### 7.11 群：`history_from` 与描述

`POST /conversations/{cid}/members` 请求 `{user_ids, history_from?: 'join'|'all'}`（其它键 400）。`join`（缺省）= 从加入起（`joined_seq = last_seq+1`）；`all` = 看到全部历史（`joined_seq = 0`），群主/群管理员的显式选择，写入成员行 `me.history_from`，系统行 `{event:'added', added:[…], history:'all'}` 让例外可见。新成员初始 `last_read_seq` 为加入通知那一行，两种方式都没有未读。
群描述：`PATCH /conversations/{cid} {description}`（管理者）—— ≤ 500 字（超出 400），空串清除；改动写系统行 `{event:'description', cleared}`；`POST /groups` 建群时可带 `description`。`conversation.*` 通知规则不变。

### 7.12 `/readyz` 的 `checks.messaging`

服务会话的进程（角色 `messages`，`all` 亦然）的就绪检查多一项 `messaging`：`{send_failed, stream_replays, stream_resyncs, stream_refused, typing_events, presence_events, search_queries, stream_connections, presence_tracked, ring_events}`，全是本进程计数（后三项是当前值），**只有数字**，不含任何名字、id 或正文；不影响就绪判定。

### 7.13 撤权矩阵（本批测试覆盖）

| 事件 | 流 | typing/presence | history/search | 附件 | 补发 |
|---|---|---|---|---|---|
| 被移出群（本进程） | 立即 `conversation removed` | 出扇出集合；`GET /presence` 不再回答该人 | 404 / 空 | 404 | 只补发 `removed` 本身 |
| 退出群 | 本人 `conversation removed` | 同上 | 404 / 空 | 404 | 同上 |
| 组织移除（账户服务进程） | ≤ 1 心跳 `conversation removed`（`detected: heartbeat`），每个会话一条，跨标签页一次 | 同上 | 404 / 空 | 404 | 同上 |
| 会话撤销 / 账户停用 | ≤ 1 心跳 `end {reason: session}` | 401 | 401 | 401 | 401 |
| 关闭 `show_presence` | `presence null` 推给此前可见者 | 双向不见 | — | — | — |

### 7.14 未做（如实）

跨进程事件外发表（多 worker、Docs 进程写卡片的前提）；组织级回执/在线/typing 开关（`organization_messaging_policies`）；阻止；@所有人；置顶消息；收藏；投票；导出；消失消息；群申请/邀请链接；FTS 回填进度显示（本批建表时同步回填，库大时首次启动会慢一次）；`sweep_unused_attachments` 仍无调度器（另批）。
