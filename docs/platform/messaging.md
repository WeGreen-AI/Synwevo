# 组织内消息：私聊与群聊（CI-26；独立应用 Synwevo Message，CI-66）

同一组织的成员之间的一对一私聊与命名群聊，带回复、表情回应、编辑/删除、附件、提及、置顶/静音/通知模式、搜索，以及跨设备同步的本人阅读位置。
自 CI-66 起是平台上的独立应用 **Synwevo Message**，有自己的源站：生产 `https://message.synwevo.com`（第十轮起；此前宣布的 `message.wegreen.ltd` 从未上线，网关 308 到新主机）（明确例外，不改成 message.synwevo.com），
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
| 发布与回滚 | 无 schema 变化。运维：隧道公开主机名 `message.synwevo.com` → 网关；`compose.yaml` 加 `messages` 服务（角色 `messages`，`SYNWEVO_APP_URL/PUBLIC_URL=https://message.wegreen.ltd`，`messages-web`/`messages`，clamd 两项）；`nginx.conf` 加 server 块（`/api/messaging/stream` 长读超时）；`client register --client-id messages-web --app messages --redirect https://message.wegreen.ltd/auth/callback --post-logout …/signed-out --backchannel …/auth/backchannel-logout`；消息进程首次启动写入注册表行。回滚：`app disable messages`（账户源随即恢复旧前缀？——**不**：410 由行的 `status='active'` 决定，停用后旧前缀在 `accounts` 角色上恢复兜底，链接回退账户源 `/messages`，但账户外壳已无该页，需回退镜像才有页面）。 |
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

跨进程事件外发表（多 worker、Docs 进程写卡片的前提）；组织级回执/在线/typing 开关（`organization_messaging_policies`）；阻止（CI-78 已做，见 7.18）；@所有人（CI-99 已做，见 7.22）；置顶消息；收藏；投票；导出；消失消息；群申请/邀请链接；FTS 回填进度显示（本批建表时同步回填，库大时首次启动会慢一次）；`sweep_unused_attachments` 仍无调度器（另批）。

### 7.15 未读提及：`unread_mentions`（CI-78）

`GET /conversations`、`GET /conversations/{cid}`（以及 `GET /sync` 的 `conversations[]`）每个会话多一个整数 `unread_mentions`：
读者楼层 `max(last_read_seq, joined_seq)` 之后、`kind='text'`、未撤回/移除/删除（`deleted_at IS NULL`）、未被本人「对我删除」、`mentions` 含读者、**发送者不是读者屏蔽的人**（§7.18）的消息数。
与 `unread` 同一楼层：读到末尾即归零；新成员（含 `history_from=all`）加入前的提及永远不计。列表一次分组查询算出（`Messaging.unread_mentions`），不逐会话查。
静音不影响这个事实（外壳决定怎么画）。**徽标 `/api/auth/messaging/unread-count` 不加 `mentions`**：平台栏只画总数，唯一的消费者（会话列表）已有逐会话字段；给共享徽标加字段要改 `synwevo_auth_client` 的共享会话路由和九处按形状断言的既有测试，不划算，形状保持 `{unread, conversations}`。

### 7.16 组织范围：`GET /conversations?organization_id=`（CI-78 起有测试与外壳）

会话按组织归属。`organization_id` 非空时服务端只返回该组织的会话；调用者不是（或不再是）该组织成员、组织不存在，一律 **200 空列表**——不是 403，不泄露组织是否存在；空串 = 全部组织（原行为）。成员资格每次读取重查（离开组织即刻为空）。
外壳：只有 `GET /organizations` 返回**多于一个**活跃组织时，在「全部 / 未读 / 已归档」旁出现组织选择；选择按账户存本机（`synwevo.msg.org.<uid>`，读写都 try/catch，存储不可用 = 全部组织），记住的组织已不可用时丢弃；新会话对话框预选当前范围；范围外打开的会话（链接、通知）仍单独读出显示。选了范围时平台栏徽标改由 `unread-count` 给总数（列表不再是全部）。

### 7.17 群头像：`PUT / DELETE / GET /conversations/{cid}/avatar`（CI-78）

| 接口 | 权限 | 说明 |
|---|---|---|
| `PUT /conversations/{cid}/avatar` | 群主/群管理员；私聊 409 `not_a_group`；已归档 409 `archived`；非成员 404（先于看图片内容） | `{image_base64}`（其它键或非字符串 400 `invalid_field`）。规则与个人、组织头像**同一个函数** `synwevo_core.avatar_image.canonical_avatar`（自 `synwevo_identity.profile` 移入共享层，账户服务照旧导入）：按字节嗅探 PNG、≤ 256 KB、≤ 512 × 512、8 位非隔行、逐块 CRC、限制解压后大小、只保留 IHDR/IDAT/IEND 重建（元数据、APNG、未知关键块、尾随字节、SVG、JPEG 等一律 400 `bad_avatar`）；不引入图像库。写 `conversation_avatars` 与系统行 `{event:'avatar', cleared:false}`（「X 更换了群头像」），成员收到 `conversation updated`。每人每小时 30 次。审计 `messaging.group.avatar`。 |
| `DELETE /conversations/{cid}/avatar` | 同上 | 没有头像 404 `no_avatar`；系统行 `{event:'avatar', cleared:true}`（「X 移除了群头像」）。审计 `messaging.group.avatar.remove`。 |
| `GET /conversations/{cid}/avatar` | **当前**成员（被移出、退出、组织撤权后 404，与不存在的会话同一答复） | `image/png`，`Cache-Control: private, no-cache`（浏览器可存但每次重新验证，所以离开的人拿不到）、`ETag`（`If-None-Match` 命中答 304，仍先查成员资格）、`X-Content-Type-Options: nosniff`、`Content-Security-Policy: default-src 'none'; sandbox`。 |

会话对象多 `avatar: {updated_at} | null`（仅群；外壳用 `?v=updated_at` 作图片地址版本）。`capabilities.group_avatar = {types: ['image/png'], max_bytes: 262144, max_side: 512}`，`limits.avatars_per_hour`。
外壳接受 PNG/JPEG/WebP：在画布里取中间正方形、按 256→192→128→96 边长编码成 PNG，直到 ≤ 256 KB 再上传——服务端照样重新校验与重建，外壳的转换不被信任。

### 7.18 屏蔽：`GET /blocks`、`PUT / DELETE /blocks/{user_id}`（CI-78）

表 `message_blocks(user_id, blocked_id, created_at, PRIMARY KEY(user_id, blocked_id))`（+ `blocked_id` 索引）。

| 接口 | 说明 |
|---|---|
| `GET /blocks` | `{items: [{user_id, name, created_at}]}`：**本人屏蔽的人**，从不包含谁屏蔽了本人。 |
| `PUT /blocks/{user_id}` | 只能屏蔽与本人**共享至少一个活跃组织**的活跃成员；本人或应用账户（`app_accounts`）400 `invalid_field`；其他任何 id（不存在、无共同组织）404 `no_user`。幂等，答 `{user_id, name, blocked: true, created_at}`。每人每小时 60 次（与解除共用）。审计 `messaging.block`。 |
| `DELETE /blocks/{user_id}` | 幂等，答 `{user_id, blocked: false}`。审计 `messaging.unblock`。 |

效果（双方之间，保护屏蔽者）：

* **私聊**：任一方屏蔽了另一方时，不能新开私聊（`POST /dm` 在两人之间尚无私聊时 403 `blocked`），已有私聊里不能发送、转发进去、上传附件、编辑本人消息或加表情回应（撤回、对我删除、取消回应、标记已读照常）。已有私聊仍可打开与阅读历史。文案：屏蔽者 `你已屏蔽对方，解除后才能发送`（`detail.by_me: true`）；被屏蔽者只得到中性的 `暂时无法向对方发送消息`、无 `detail`。
* **在线、正在输入、已读回执**：两人互相为 `null` / 不发送 / 不列入 `read_by`、不推 `receipt`；与第三人照常。屏蔽与解除时服务端向双方开着的标签页各推一条 ephemeral `presence`（屏蔽后 `null`，解除后真实状态）——与对方关闭在线开关时的事件相同，不透露屏蔽。
* **共同群**：被屏蔽者的消息与提及照常可见（群是共享空间），但**不**产生屏蔽者的收件箱条目（`recipients_for` 跳过；`conversation.added` / `conversation.announcement` 在行为人被屏蔽时也不写）、不计入 `unread_mentions`（不出 @），摘要邮件寄出前复核（`Notifier.conversation_unread`）不计被屏蔽者的消息，因此只有他的未读时为 `nothing unread`，全文预览也不会取他的那条。平台徽标的 `unread` 仍计入这些可见消息。
* **不告知被屏蔽者**：会话对象只有 `peer.blocked_by_me`（本人是否屏蔽了对方），从不暴露对方是否屏蔽了本人；屏蔽只向屏蔽者自己的设备推 `conversation updated`。
* **举报不受影响**：屏蔽者仍可举报被屏蔽者的消息，组织审核流程不变。
* **账户**：`POST /api/auth/export` 的结果多 `message_blocks: [{user_id, created_at}]`（仅本人的列表）；账户删除到期（`finalize_deletions`）删除本人的列表**以及**指向本人的行（被删除的账户再也不能发送，留下的无名行只会挂在别人的列表里）。

`capabilities.blocks = true`、`limits.blocks_per_hour`。

### 7.19 私聊 → 新群（CI-78，外壳）

私聊不能加人（`POST /conversations/{cid}/members` 仍是 409 `not_a_group`）。私聊信息面板的「用这个人建群」打开新群对话框：组织 = 私聊所在组织，对方已选，并明示「之前的私聊记录不会带入新群」；创建走普通 `POST /groups`（`history_from` 不适用、新群只有创建行）。无接口改动。

### 7.20 发布与回滚（CI-78）

schema **v26**：只加表 `conversation_avatars`、`message_blocks`（`CREATE TABLE IF NOT EXISTS`，随 `messaging.SCHEMA`），既有表、列、CHECK 不动。回滚到 v25 代码：两表闲置——屏蔽不再生效、群头像不再显示，其它行为不变。彻底清除：`DROP TABLE message_blocks; DROP TABLE conversation_avatars;`。无回填、无新环境变量。验收：`apps/messages/tests/test_messaging_ci78.py`（8 项）、`tests/integration/test_messaging_ci78_composed.py`（4 项：屏蔽让到期摘要以 `nothing unread` 跳过且群提及不成邮件、导出只含本人列表与删除清两方向、组合源新路由、头像规则同一函数）、外壳 `tests/ci78.test.ts`（3 项）；浏览器证明见 `messaging-ui.md` §7。
### 7.21 拒绝怎样呈现给人（CI-83）

共享错误呈现（`@synwevo/auth-client/api-messages` 的 `apiFailureText`）只在外壳目录里**有**服务端原句时显示它，否则退回按代码的通用句，最后是「请求未完成（code）」。所以：

| 拒绝 | 呈现 | 要求 |
|---|---|---|
| 固定原句（群主退出前须交接、会话已归档、只能撤回自己的消息……） | `apps/messages/web/src/messages.messages.ts` 里的三语言条目 | 新增用户能碰到的拒绝，同一提交里补译文 |
| 带数字的原句（撤回/编辑时限、正文/群名/群描述长度、成员上限、转发目标数、附件/语音大小） | 外壳 `lib/refusals.ts` 按代码从 `detail` 取数字（`recall_window_s`、`edit_window_s`、`limit`，语音另带 `kind: "voice"`） | 服务端在 `detail` 里带上这个数字；撤回与编辑时限是部署配置，按原句匹配会在改配置后退回代码 |
| 只有 API 客户端能造成的字段形状错误（`client_id 无效`、`只能设置 text`……） | 原句与代码给 API 调用方；外壳不会发这些字段 | 列在 `apps/messages/web/tests/refusals.test.ts` 的「仅 API」清单并写明理由 |
| 共享代码（`unauthorized`、`too_many_attempts`） | 共享呈现的通用句 | — |

`refusals.test.ts` 读消息服务的全部 `ServiceError(...)`，三类之外的新拒绝会让测试失败。对象页（`/objects/{app}/{kind}/{id}`）只在重试能改变结果时（网络、5xx、408、429、前置网关）给「重试」；`no_organization`、`organization_unavailable`、`archived` 给「返回工作空间」。
Docs 只在当前工作空间属于组织时提供「在消息中讨论」——个人工作空间的项目没有组织会话（§8）。

### 7.22 @所有人（CI-99）

`POST /conversations/{cid}/messages` 多一个布尔字段 `mention_all`。

| 规则 | 行为 |
|---|---|
| 谁能用 | 只有**群**的群主与管理员，且不是应用对象的讨论（§8.1：那里的成员是所有能打开对象的人，群主只是第一个打开讨论的人）；普通成员 403 `mention_all_forbidden`（`detail.role`），对象讨论 403（`detail.object`），私聊 400 `invalid_field`（`field=mention_all`），非布尔值 400。会话输出里的 `can_mention_all` 按读者给出这个判断，外壳只在它为真时提供 |
| 重试 | 权限检查在 `client_id` 去重之后：确认丢失后被降级的人重试，拿回已存的那条消息，而不是一个永远过不去的 403 |
| 存储 | `messages.mentions` 里多一个记号 `@all`（永远不是用户 id）；接口里是 `mention_all: true`，`mentions` 仍只列用户 id |
| 未读提及 | 计入除发送者外每位成员的 `unread_mentions`，规则与点名提及相同：读位之后、未撤回/移除/删除、未被本人「对我删除」、发送者不是读者屏蔽的人 |
| 收件箱 | 每位成员按**自己的**设置收到 `conversation.mention`：`notify=none`、静音中（含定时静音）、屏蔽了发送者的人不收；邮件与点名提及同为摘要（默认关闭） |
| 外壳 | 可用时，只输入 `@` 或点 @ 按钮，列表第一项是「所有人（N 人）」，但默认高亮第一个人；输入名字后「所有人」排在人之后（只在「所有人 / all / everyone」的开头匹配时出现）——回车不会误发给全员。选中后正文插入 `@所有人`；发送时是否带 `mention_all` 只看正文里是否还有 `@所有人`（或英文界面的 `@Everyone`），所以切换会话后恢复的草稿照样有效，删掉这段文字就不再带。编辑消息时不弹出提及列表 |
| 系统行 | 创建群、改名、加人等系统行记录的是操作者本人的动作：操作者读到上一条（按 `max(last_read_seq, joined_seq)`）时，这一行也记为已读，并清掉「标记未读」，与本人发消息相同 |

取舍：团队协作产品通常把「@所有人」限定给群主/管理员（或由管理员授权），以免一人刷屏全员；这里不另设开关，也不绕过个人的静音——
一个人静音了群，就是不想被群里的消息打扰，@所有人仍会在会话列表里留下 @ 标记。

## 8. 应用联动：Synwevo Docs ↔ Synwevo Message（CI-75）

Docs 对象（工作空间范围）在消息（组织范围）里有一个**对象会话**；Docs 的真实事件经**外发表**变成会话里由应用身份发出的**卡片**；
卡片**逐读者渲染**，会话成员身份从不授予对象访问权。实现：`synwevo_core/messaging_bridge.py`（表、规则、渲染、worker）、
`apps/messages/synwevo_messages/bridge_routes.py`（接口与 worker 的启动）、Docs 侧的三处写入点（下文 8.2）。

**决策记录。**

| 项目 | 结论 |
|---|---|
| 问题与基线 | 消息与 Docs 互不知晓：项目的讨论只能建普通群再手贴链接（CI-31 链接卡片只解析正文里的 URL）；评论、任务结束、复制审批只进通知中心，没有「人在说什么」的地方；Docs 进程写不了消息（事件扇出在消息进程内存里）。 |
| 成熟参考 | 第九轮研究基线 3.2「卡片与动作」：卡片按读者渲染、动作幂等、幂等键（IETF Idempotency-Key 草稿；企业微信模板卡片 `task_id` 唯一）；Slack/Teams 的「应用作为发送者」不冒充人。跨进程用 SQLite 外发表（3.4-6），不引入中间件。 |
| 对比与选择 | (a) Docs 进程直接写 `messages` 行——绕过消息进程的扇出，开着的标签页看不到；(b) Docs 调消息 API——多源无 CORS，Docs 服务端拿不到用户的消息源会话，且会出现第二个写入源；(c) **本方案**：Docs 在业务事务里写 `messaging_outbox` 一行（确定性 `event_id`），消息进程的 worker 读表、按对象**当时**的状态生成卡片、写 `messages` + `message_cards`、按**当前活跃成员逐人**扇出。对象会话由消息源的接口按对象自身的授权创建；Docs 只把人送过去（`messagesHref`），不调用消息 API。保留：`messages.kind` 取值、`seq`/`client_id` 幂等、`joined_seq` 楼层、组织成员资格每次重查。 |
| 预期变化 | Docs 项目页「更多」菜单与任务页顶栏出现「在消息中讨论」（仅当部署有 Synwevo Message）；点击到消息源 `/objects/{app}/{kind}/{id}`，外壳调接口后落到 `/c/{cid}`。评论、任务结束、复制申请决定/结果在对应对象会话里出现卡片（发送者显示为 Synwevo Docs，`sender_kind=app`）。 |
| 边界与风险 | 卡片不产生任何 `conversation.*` 收件箱条目（事件自己的 `docs.*`/`copy.*` 通知不变）；不含 `mentions`；正文不含对象标题；读者无权 → 中性标题、无链接；链接只由注册表（Docs 地址）与 `SYNWEVO_ISSUER`（审批页）拼出。不做：协同编辑/保存/按键事件、分享或权限变更（Docs 今天没有这类事件）、事件自动建群。群成员上限沿用 200（超过即 `too_many_members`，不静默截断）。 |
| 验收 | `apps/messages/tests/test_bridge.py`（5）、`apps/docs/tests/test_messaging_outbox.py`（2）、`tests/integration/test_messaging_bridge.py`（2，`all` 角色、真实 runner 与复制执行）；隔离实例四进程证明见 verification.md §16.75。 |
| 发布与恢复 | schema **v24**：新表 `object_conversations`、`messaging_outbox`、`message_cards`、`app_accounts`，`conversations` 加可空列 `purpose`；全部 `CREATE IF NOT EXISTS` / 守卫 `ADD COLUMN`。旧代码读新库：新表闲置、`purpose` 被忽略、应用账户是一条普通 `users` 行（无口令，不会被登录）。回滚 SQL：`DROP TABLE object_conversations; DROP TABLE messaging_outbox; DROP TABLE message_cards; DROP TABLE app_accounts;`（卡片消息行保留为普通文本行）。无新环境变量；`all` 的组装层把 `/objects` 交给消息外壳。 |

### 8.1 对象会话

| 表 / 列 | 含义 |
|---|---|
| `object_conversations(app, kind, object_id, conversation_id UNIQUE, workspace_id, organization_id, created_by, created_at)` | 对象 ↔ 会话的稳定映射，主键 `(app, kind, object_id)`；`kind` 只有 `project` 与 `copy_request`（任务解析到它的项目） |
| `conversations.purpose` | `object` = 为对象而存在的群；NULL = 普通群。叠加在 `kind='group'` 之上，不改 CHECK |

规则（每条都有测试）：

* **谁可打开对象，谁在会话里。** 项目：所在工作空间的活跃成员，且仍是该工作空间所属组织的成员（与 `docs_links.py` 同一判定）；复制申请：申请人，或持 `org.approvals.decide` 且管理目标工作空间的人（`ProjectCopies.can_decide`）。
* **首次 `POST` 创建**：调用者成为群主，其余可打开对象者成为成员（`notify='mentions'`），群名取对象标题、`workspace_id` = 项目工作空间、`purpose='object'`；检查与创建在一个写事务里，两个「第一个」调用者只会得到一个群。
* **后来者加入**：可打开对象但不在会话里的人 `POST` 即加入（`joined_seq` 从现在起，系统行「Synwevo Docs 把 X 加入了群聊」）。工作空间的新成员**不会**被自动加入，直到他们自己点「在消息中讨论」。
* **失去对象访问权就离开会话。** 被移出工作空间（仍在组织）、工作空间或组织不再活跃的成员，由 worker 在每次卡片投递后与每 60 s 的巡检里关闭其成员资格（`left_at`，系统行 `removed` 带 `reason=object_access_lost`，行为人是应用身份）；群主失去访问权时群交给最早加入的剩余成员（先看管理员），无人剩余则归档。在被移除之前，他读到的卡片已是中性形态（8.3）。群名是创建时取的对象标题，此后是普通群名（群主可改）。组织移除仍走 `withdraw_from_organization`，即刻生效。
* 个人工作空间的项目没有组织 → `409 no_organization`；对象不存在或调用者不可打开 → **`404 no_object`**（不是 403，响应里没有标题）；未知应用/种类 → 同样 404。

| 接口（消息源 `/api/messaging`） | 权限 | 说明 |
|---|---|---|
| `GET /objects/{app}/{kind}/{id}/conversation` | 可打开对象且是会话成员 | `{object, conversation, created:false, joined:false}`；无会话 → `404 no_conversation` |
| `POST /objects/{app}/{kind}/{id}/conversation` | 可打开对象 | 创建 / 加入 / 返回；`created`、`joined` 说明发生了什么；每人每小时 60 次；审计 `messaging.object.open` |
| `GET /conversations/{cid}/object` | 会话成员 | 这个会话关于什么：可打开 → `{app, app_name, kind, id, title, href, workspace_id}`；不可打开 → `{app, app_name, kind, title: <中性>, degraded: true}`；普通会话 → 404 |

`app` 今天只有 `docweave`（Synwevo Docs 的注册表 id）；`kind` ∈ `project` / `job` / `copy_request`。`object.href` 与卡片动作一样只由注册表拼出（8.3）。

**外壳路径契约（消息外壳实现）。** `/objects/{app}/{kind}/{id}`（自己的源上；`all` 角色下在共享源同一路径，组装层已把 `/objects` 交给消息外壳）：
页面 `POST /api/messaging/objects/{app}/{kind}/{id}/conversation`，成功即 `navigate('/c/' + conversation.id)`；`404` 显示「找不到该对象，或你没有权限查看它」并给「返回会话列表」；
`409 no_organization` / `organization_unavailable` 显示服务端文案；`429` 显示重试时间。Docs 的入口生成的是 `messagesHref(state, {path: "/objects/<state.app_id>/project/<pid>", lang})`
（跨源 = `<消息源>/auth/start?next=/objects/…&lang=…`，同源 = 纯路径），任务页用 `/objects/<app_id>/job/<jid>`（解析到项目会话）。群信息抽屉可用 `GET /conversations/{cid}/object` 显示关联对象与「在 Synwevo Docs 中打开」（`degraded` 时只显示中性标题）。

### 8.2 外发表与 worker

`messaging_outbox(id, app, event_id UNIQUE, kind, object_kind, object_id, workspace_id, organization_id, actor_id, payload JSON, created_at, delivered_at, attempts, last_error)`。
`object_kind/object_id` 是**接收卡片的对象**（评论与任务 → 项目；复制事件 → 申请本身）；卡片关于什么在 `payload` 里。`payload` 只有 id，**不复制正文与标题**（投递时再读）。
写入用 `INSERT OR IGNORE`：同一 `event_id` 重试永远只有一行、一张卡。

| 事件 `kind` | 写入点（与业务变更同一事务） | `event_id` | 卡片 `object` / `state` |
|---|---|---|---|
| `docs.comment` | `synwevo_docs/comments.py::create`（含回复） | `comment:{cm_id}` | `comment` / `open` \| `resolved`（投递时读） |
| `docs.job_finished` | `synwevo_docs/jobs.py::_finalize_tx`（与 `docs.job_finished` 通知同处；机器发起的任务也写） | `job:{jid}:{run_no}` | `job` / `completed` \| `completed_with_errors` \| `failed` \| `cancelled` |
| `copy.decided` | `synwevo_core/project_copies.py::decide`（账户服务的审批路由与 Docs 的自批都经它） | `copy:{pc_id}:approved\|rejected` | `copy_request` / 同 status |
| `copy.finished` | `synwevo_core/project_copies.py::finish`（Docs 的复制执行器调用） | `copy:{pc_id}:done\|failed\|stale` | `copy_request` / 同 status |

不写外发表的：协同编辑的每次变更/保存、评论编辑与删除、任务开始/取消请求、项目改名/归档（它们要么是噪音，要么今天没有事件）。

**worker**（`BridgeWorker`，在 `messages` / `all` 进程里由 `bridge_routes.install` 启动，登记于 `synwevo_core.workers`，随 lifespan 停止）：每 250 ms 读一次 `delivered_at IS NULL AND attempts<10` 的前 50 行（`all` 上被写入唤醒）；
每行：无映射 → `delivered_at` 置位、`last_error=skipped_no_conversation`（**事件不建会话**）；会话已归档 → `skipped_archived`；对象已删 → `skipped_object_gone`；否则按对象当前状态生成卡片，
在一个事务里写 `messages`（`kind='text'`，`sender_id` = 应用账户，`client_id` = 外发行 id，所以「已写消息但没来得及标记」的重试命中 `UNIQUE(conversation_id, sender_id, client_id)` 不会出第二张卡）、
`message_cards`、标记投递；然后对**当前活跃成员逐人** `publish`（每人的事件里是他自己那份渲染）；最后对该会话做一次成员资格巡检（8.1）。异常 → `attempts+1`、`last_error`，下一拍重试，10 次后停止重试（行保留可查）。没有消息应用的部署（账户源兜底旧前缀）没有这个 worker：Docs 照常写外发表，行保持未投递；应用注册并启动后，worker 把它们按当时是否有对象会话投递或标为 `skipped_no_conversation`。

### 8.3 卡片契约（外壳按此渲染）

`_message_out` 对每条消息多出 `sender_kind: "user" | "app"`；卡片消息另有 `card`，形状：

```json
{"app": "docweave", "app_name": "Synwevo Docs", "title": "合同项目", "subtitle": "Ben Member: 这个字段取哪一列？",
 "state": "open", "object": {"kind": "comment", "id": "cm_…"},
 "actions": [{"label": "查看评论", "href": "https://<Docs 地址>/p/p_…?comment=cm_…"}]}
```

* **逐读者渲染**（`history`、`search` 与 worker 逐人 `publish` 的事件里）：读者可打开对象 → 完整卡片，`subtitle` 与动作 `label` 用读者语言（`users.locale`）；读者不可打开 → `{"app", "app_name", "state", "title": <中性标题>, "object": {"kind"}, "degraded": true}`，**没有 id、没有 subtitle、没有 actions**。中性标题：你没有权限查看的文档 / 你沒有權限查看的文件 / A document you cannot open。
* **没有 `card` 字段**的情形：消息已删除；事件面向所有人而非某个读者（表情回应、编辑、删除引起的 `message_updated`）——外壳保留手头已渲染的卡片、只更新其余字段，需要时按 `seq` 重读 history。
* `body` 是纯文本回退（列表预览、搜索、不认识卡片的外壳）：zh-Hans 源文，只说事件种类与状态，**不含对象标题**，如「Synwevo Docs · 生成任务已完成」；有 `card` 时外壳渲染卡片而不是 `body`。
* `title` 与 `state` 是投递时读到的对象状态；之后改名不回写（像一条引用了当时名字的消息）。`state` 是稳定键，标签由外壳翻译：

| `object.kind` | `state` | zh-Hans | zh-Hant | en |
|---|---|---|---|---|
| `job` | `completed` / `completed_with_errors` / `failed` / `cancelled` | 已完成 / 部分失败 / 失败 / 已取消 | 已完成 / 部分失敗 / 失敗 / 已取消 | Completed / Completed with errors / Failed / Cancelled |
| `comment` | `open` / `resolved` | 待处理 / 已解决 | 待處理 / 已解決 | Open / Resolved |
| `copy_request` | `approved` / `rejected` / `done` / `failed` / `stale` | 已批准 / 被拒绝 / 已复制 / 失败 / 需重新申请 | 已批准 / 被拒絕 / 已複製 / 失敗 / 需重新申請 | Approved / Rejected / Copied / Failed / Needs a new request |

* 动作 `href` 由服务端拼：Docs 路径（`/p/{pid}`、`/p/{pid}/jobs/{jid}`、`/p/{pid}?comment={cid}`）接注册表里 Docs 的启动地址（`docs_links.docs_origin`），审批页接 `SYNWEVO_ISSUER`；地址未知时该动作**不出现**，不猜主机。动作标签：在 Synwevo Docs 中打开 / 查看任务 / 查看评论 / 查看审批（三语言在 `messaging_bridge.ACTION_LABELS`）。任务卡的 `subtitle` 是模板 `{done} 份成功，{failed} 份失败`（三语言）。
* 卡片消息**没有 `mentions`**，不产生任何 `conversation.dm/mention/group` 收件箱条目，也不走 `Messaging.send`（因此无需 `silent` 参数——它们根本不经过 `recipients_for`/`_notify`）；它们照常计入未读。事件自己的 `docs.comment`/`docs.mention`/`docs.job_finished`/`copy.*` 通知不变：一个事件 = 一条收件箱项 + 订阅会话里的一张卡片。

### 8.4 应用身份

`app_accounts(user_id PK, app UNIQUE, created_at)`：每个应用一条 `users` 行（`status='active'`、`display_name` 取注册表显示名、邮箱 `<app_id>@apps.synwevo.invalid`——由 app_id 派生，RFC 2606 保留域，永远没有邮箱），
无口令、无个人工作空间、不是任何组织或工作空间的成员（所以不在目录、不能被私聊：`directory` 与 `open_dm` 按组织成员资格答 404）。`Sessions.create` 在铸造任何会话前查 `app_accounts`，
命中即 `403 app_account`——无论口令、Passkey、邮件验证码还是外部身份，都到不了这一步之后。由消息进程启动时 `ensure_app_account` 幂等创建；`_message_out` 用 `sender_kind='app'` 标出。
平台管理员的用户列表里能看到这条账户（名为 Synwevo Docs），这是事实呈现，不是漏洞；不要停用它（它不参与任何授权）。

### 8.5 安全要点（对照 §十）

逐读者渲染在读侧（`decorate_message`），不在写侧；引用（`_access`）不随卡片输出；`payload` 不参与拼链接；非成员一律 404；组织成员资格由 `Messaging._member` 每次重查；
工作空间成员资格丢失 → 卡片即刻中性、≤ 60 s 或下一张卡片时离开会话（`test_a_reader_who_lost_the_object_sees_a_neutral_card_and_then_loses_the_conversation`、集成用例 bob 段）；
限额：对象会话打开 60/人/小时；日志不记录正文与标题。
