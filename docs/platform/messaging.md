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
