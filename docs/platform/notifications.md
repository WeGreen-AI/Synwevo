# 平台通知（站内通知中心 + 邮件投递）

> 状态：2026-09-26 实现（CI-22）。本文写的是**当前代码的行为**；未做的部分在 §7。

## 0. 出发点：现状有什么、没有什么（实现前）

| 已有 | 位置 | 缺口 |
|---|---|---|
| 邮件外发队列 `mail_outbox`（排队 → 工作线程发送 → 重试/失败可见） | `synwevo_identity/mail.py`，管理端 `/api/admin/mail` | 只有邮件，没有站内记录；用户无法在产品内看到「发生了什么」 |
| 安全邮件：改口令、改邮箱、重置口令、Passkey/两步验证/恢复码变化、外部登录关联 | `notify_security_change`、`account_routes`、`external.py` | 没有偏好、没有历史 |
| 邀请邮件 | `members.py:issue_invitation` | 受邀者已有账户时也只能等邮件；邀请人不知道对方接受/拒绝 |
| 角色变更、移出空间、退出、Docs 任务完成、文件被拒 | 只有审计行 | 当事人完全没有提示 |
| Toast（页面内即时反馈） | `@synwevo/ui ToastProvider` | 刷新即消失，不是通知 |

没有：任何通知表、未读计数、偏好、Web Push、Service Worker。

## 1. 决策记录

| 项目 | 结论 |
|---|---|
| 问题与基线 | 发生在用户身上的事（被邀请、角色变了、被移出、口令被改、文件被拒）只有邮件或只有审计行；邀请人不知道结果；用户没有一个地方看到待处理事项。 |
| 成熟参考 | Slack 通知设置（读取 2026-09-26）：两档触发「Everything / Mentions and direct messages」、按会话例外、通知时段（notification schedule）、预览开关；Linear Inbox（读取 2026-09-26）：集中收件箱、逐条/全部标记已读（`mark all notifications as read`）、`Show unread first`、点击在收件箱视图里打开对象、通知来自订阅；Notion 企业管理（读取 2026-09-26）：「Admin roles don't change what someone can see or edit」——管理员看不到别人的内容，通知也一样。 |
| 对比与选择 | (a) 只加邮件：用户没有历史与待办；(b) 前端 localStorage 记录：换设备即丢、服务端无法投递；(c) 独立消息中间件：本项目单库单机，新增基础设施无依据。选：核心包里一张 `notifications` 表 + 一张投递表 + 一张偏好表，所有角色读同一张表；邮件继续走已有 `mail_outbox`。渲染在读取时按读者语言进行（存模板键与参数，不存译文），用户生成的名字保持原文。 |
| 预期变化 | 三个外壳的顶栏出现铃铛与未读数，抽屉列表分「全部 / 未读 / 待处理」，可逐条/全部标记、归档、点击进入对象（服务端给的链接）；账户页新增「通知」节：各类别站内开关与邮件方式（立即/汇总/关）、免打扰时段与时区、邮件预览详细度；安全通知始终开启。接口：`/api/auth/notifications*`（每个角色都提供）。 |
| 边界与风险 | 读通知不改业务状态（接受邀请仍在邀请页）；邮件是投递行而不是"已送达"；数字库是唯一队列，多实例一致（认领在事务内翻转状态）；不记录消息正文/文档内容，参数只有名字与计数；Web Push 未做（§7）；邀请人只在受邀者**已有账户**时收到站内邀请项（否则只有邮件）。 |
| 验收 | §6：真实事件产生行、读者语言渲染、只有本人能读/标记、`read-all` 只到打开列表时的游标、偏好关掉类别则不落行但安全例外、投递恰好一次且分为立即/汇总/免打扰、邮件未配置时投递标为 skipped 而非丢失、路由层邀请→通知→标记不接受邀请。 |
| 发布与恢复 | schema v8→v9 只新增三张表（`CREATE IF NOT EXISTS`），旧数据不动；账户服务多一个后台线程（`notify`），只在有 mailer 时有意义；回滚只需回退代码，表保留无害。 |

## 2. 数据契约（`synwevo_core.notifications`）

| 表 | 关键列 | 说明 |
|---|---|---|
| `notifications` | `seq`（游标）、`id`、`user_id`、`kind`、`category`、`severity`（info/action/critical）、`organization_id`/`workspace_id`/`object_kind`/`object_id`、`dedupe_key`（同一读者唯一）、`params`（JSON 纯字符串）、`target`（`{origin, path}`）、`action_state`（pending/done/void）、`read_at`、`archived_at` | 每个接收者一行；标题正文在读取时按读者语言由目录渲染 |
| `notification_deliveries` | `notification_id`、`channel=email`、`status`（pending/queued/skipped/failed）、`not_before`、`attempts`、`mail_id` | 邮件渠道的 outbox；`queued` 带 `mail_outbox` 的 id，可从管理端邮件页追踪 |
| `notification_preferences` | `categories` JSON `{类别: {in_app, email, push}}`、`quiet_start/quiet_end/timezone`、`preview` | 缺省：全部站内开、邮件立即；docs 缺省汇总；**conversation 缺省关**（只能选「未读摘要」或关，不能逐条）；`preview` 缺省 `minimal`——邮件/推送不带消息正文（CI-68） |

链接（`target`）只有 `origin ∈ {accounts, portal, docs}` 与以 `/` 开头的路径，由服务端根据本部署的 `SYNWEVO_ISSUER`/`PORTAL_URL`/`APP_URL` 拼成绝对地址；客户端从不拼接。

## 3. 事件目录（本轮接入）

| kind | 类别 / 严重度 | 接收者 | 触发点 | 邮件缺省 | 点击目标 |
|---|---|---|---|---|---|
| `security.change{what}` | security / critical | 账户持有人 | 改口令、重置口令、改邮箱、Passkey 增删、两步验证开关、恢复码重生成、外部登录关联/解除 | 立即，不可关 | 账户页 → 登录与安全 |
| `wallet.low_balance` | platform / info | 钱包持有人本人 | 账户服务后台清扫发现可用余额（已入账余额减未过期预留）低于本人在钱包页设的提醒线；每次跌破一次，回到提醒线以上后重新生效；认领与通知同一事务（`billing/reminders.py`，见 `docs/personal-wallet.md`） | 立即 | 账户 → 钱包 |
| `invitation.received` | organization / action | 受邀者（已有账户） | 发出/重发邀请（`dedupe_key=invite:{id}`，待处理） | 立即 | 引导页（列出等待接受的邀请，链接仍在邮件里） |
| `invitation.accepted` / `invitation.declined` | organization / info | 邀请人 | 受邀者接受 / 拒绝；同时把受邀者的待办置为 done / void | 汇总 | 空间成员页 |
| `invitation.revoked` | organization / info | 受邀者（已有账户） | 邀请被撤回；待办置为 void | 不发 | 引导页 |
| `member.role_changed` | organization / info | 当事成员 | 空间角色变化（升降都通知） | 立即 | 账户页 → 工作空间 |
| `member.removed` | organization / info | 当事成员 | 被移出空间 / 被移出组织 / 平台管理员撤销历史数据的指派（执行者写作「平台管理员」——`actor_kind`，不写管理员的邮箱或姓名；执行者本人不通知） | 立即 | 账户页 |
| `member.left` | organization / info | 空间的 owner/admin | 成员自行退出 | 汇总 | 空间成员页 |
| `organization.role_changed` | organization / info | 当事成员 | 组织角色变化 | 立即 | 组织页 |
| `docs.file_rejected` | docs / action | 上传者（登录用户；机器上传无人可通知） | 来源文件被安全检查判定为恶意并拒绝（`dedupe=source:{sid}:rejected`；同内容再传即拒，不重复通知）（CI-29） | 立即 | Docs → 项目页 |
| `docs.mention` | docs / action | 评论里被 `@` 的成员（须是项目所在空间的活跃成员；作者不通知） | 项目评论提及（`dedupe=comment:{id}:mention:{uid}`；编辑评论只通知新增的提及）（CI-33） | 立即 | Docs → 项目页 `?comment=<id>`（打开抽屉并高亮） |
| `docs.comment` | docs / info | 该项目此前评论过、仍在空间内的成员（作者与被提及者除外） | 项目新评论或回复（`dedupe=comment:{id}:participant:{uid}`）（CI-33） | 汇总 | Docs → 项目页 `?comment=<id>` |
| `docs.job_finished` | docs / info | 任务发起人（登录用户） | 生成任务结束：完成 / 部分失败 / 失败 / 已取消，附成功与失败份数；每次运行一条（`dedupe=job:{jid}:{run_no}`），与终态在同一事务写入（CI-29） | 汇总 | Docs → 任务页 |
| `conversation.dm` | conversation / info | 私聊对方 | 私聊里的新消息；同一会话在读者读到上次位置之前只产生一项（去重键含读者的 last_read_seq）；读者静音、「不通知」或定时静音 `muted_until` 未到期时不产生（CI-74，`recipients_for`） | 关；读者开启后为未读摘要 | 消息 → 该会话 |
| `conversation.mention` | conversation / action | 被提及的成员 | 群聊或私聊里被 @ 提及；静音或「不通知」的会话不产生 | 关；读者开启后为未读摘要（提及不能绕过私信规则） | 消息 → 该会话 |
| `conversation.group` | conversation / info | 把群设为「所有消息」的成员 | 群里的新消息（默认「仅提及」，因此默认不产生） | 关 | 消息 → 该会话 |
| `conversation.added` / `conversation.removed` | conversation / info | 被加入或移出群的人 | 群成员变化 | 汇总 / 关 | 消息 → 该会话 / 消息列表 |
| `conversation.announcement` | conversation / info | 群的其他成员 | 群主/群管理员写了群公告（清除不发；去重键含公告时间） | 汇总 | 消息 → 该会话 |
| `conversation.reported` | conversation / action | 持 `org.messaging.manage` 的组织成员（举报人本人除外） | 成员举报了一条消息；正文不含原因与内容，只说去组织页处理 | 立即 | 组织页 → 举报处理（`?report=<id>` 聚焦该条） |
| `conversation.report_removed` / `conversation.report_dismissed` | conversation / info | 举报人 | 举报已处理：消息已删除 / 消息保留；正文是处理人的说明 | 汇总 | 消息 → 该会话 |
| `member_import.finished` | organization / info | 发起导入的人 | 一次批量导入结束（完成/部分完成/失败/取消），附邀请/更新/跳过/失败计数 | 汇总 | 组织页 → 批量导入成员（`?import=<id>` 打开该批次） |
| `member_import.roles_applied` | organization / info | 接受邀请的新成员 | 导入指定的组织自定义角色在其接受邀请时由导入者身份分配成功 | 汇总 | 组织页 |
| `copy.requested` | approval / action | 能决定该申请的成员（持 `org.approvals.decide` 且是目标空间 admin/owner） | 个人项目申请复制到组织工作空间（`dedupe_key=copy:{id}:requested:{reader}`）；申请人自行批准时不发 | 立即 | 组织页 → 审批（`?approval=<id>` 展开该申请） |
| `copy.approved` / `copy.rejected` | approval / info | 申请人 | 目标空间决定了申请；拒绝附理由 | 立即 | Docs → 源项目（对话框里显示状态） |
| `copy.done` | approval / info | 申请人与决定者 | Docs 执行器已在目标空间创建副本 | 汇总 | Docs → 源项目 / 组织页 → 审批 |
| `copy.failed` | approval / action | 申请人与决定者 | 执行前复核失败（来源已变、授权丢失、批准过期）或复制出错；附原因 | 立即 | Docs → 源项目 / 组织页 → 审批 |

不发的：普通登录成功、任务进度百分比、每次路由切换——目录里没有这些 kind，就不会有人凑出通知。审批、成员导入、消息三个类别随各自功能接入。

## 4. 邮件投递（`synwevo_identity/notify.py`）

账户服务的 `NotificationDispatcher` 每 30 秒取 `pending` 且到期的投递：读者不存在/已禁用 → skipped；读者在免打扰时段（按其时区，安全通知除外）→ `not_before` 推到时段结束。每位读者的当前资格、偏好和摘要预算在同一写事务内重查；标记 `queued`、`Mailer.enqueue` 与全部 `mail_id` 关联在同一保存点内提交，任一步失败就一起回滚。这里是本地入队，SMTP 由邮件 worker 随后执行；`queued` 不表示已寄出。同一读者同一批里的多条普通通知合成一封汇总；`critical` 单独立即寄。邮件未配置 → skipped（原因可见）；入队失败 → 退避重试 5/15/60/240 分钟，第五次失败转为 failed。`digest` 偏好只是把 `not_before` 推后 30 分钟，让同期事件落到同一批。

**寄出前复核（CI-68）。** 非安全投递在真正寄出前再检查一次（`Notifier.stale_reason`）：读者已在站内读过/归档 → skipped `read in app`；该类别邮件此刻已关 → `channel off`；会话类条目再看读者在该会话的实时状态——已不是成员 → `no longer a member`、已静音、「不通知」或定时静音 `muted_until` 尚未到期（CI-74）→ `conversation muted`、读游标之后没有未撤回的消息（读完了，或唯一的未读被撤回）→ `nothing unread`。项目讨论的工作空间（或其组织）此刻不可用——冻结、删除（仍在恢复期内，或已过期而巡检尚未运行）——或该讨论里某条消息的举报（`object_kind = message_report`：举报 → 消息 → 会话；管理者的 `conversation.reported` 与举报人的处理结果），或内容属于某个工作空间的条目（`Notifier.WORKSPACE_CONTENT_CATEGORIES`：Docs 类 `docs.*`、自动化类 `forms.*`（表单所在空间）、审批类 `copy.*`（复制的目标空间），都带 `workspace_id`）所属工作空间不可用 → `workspace_unavailable`（A44）：站内每次读取都被拒绝，邮件与推送也不带出会话名、未读数、正文、表单名或项目名；条目留在收件箱，记为 skipped 而不是推迟，工作空间回来后的新条目照常寄出。组织类条目（`invitation.*`、`member.*`、`organization.*`）虽然也带 `workspace_id`，但说的是读者**自己**的成员身份（被邀请、角色变更、被移出），与工作空间此刻是否可用无关（`workspace_availability` 不判断个人身份），照常寄出。`copy.failed` 按目标空间判断：目标空间被冻结或删除而导致复制失败时，申请人的失败通知只留在收件箱，不寄邮件、不推送（A44 决定：通知内容含目标空间与项目名，而该空间此刻不可用；申请人仍可在 Docs 源项目里看到结果）。推送队列走同一复核。

**私信邮件是选择加入的未读摘要，不逐条寄。** `conversation` 类别的邮件缺省关；读者在「账户 → 通知」开启后只能是「未读摘要」（`set_preferences` 拒绝 `immediate`；旧行里的 `immediate` 读作 `digest`）。摘要寄出前额外限速：同一读者按当前平台配置限制两封会话摘要的最短间隔及滚动最近24小时上限，默认30分钟、8封（`conversation_digest_hold`，按不同outbox邮件计，不按条目；这是入队预算，不是最终收件证明），超出的投递推到可寄时刻。摘要条目按寄出时的实时状态渲染（`render_external`）：标题是「『X』有 N 条未读消息」，正文默认为空；读者把 `preview` 改为 `full` 才附最新一条未读的发送者与前 120 字。v23 迁移把旧偏好行里 conversation 的 `digest/immediate` 重置为 `off`——那是缺省值被一起保存下来的，不是读者的选择。

## 5. 界面

* `@synwevo/auth-client` 的 `NotificationBell`：铃铛 + 未读数（打开时、每 60 秒可见时、切回标签页时刷新，无长连接），抽屉列表三档筛选、逐条已读/未读/归档、「全部标为已读」只到打开列表时的 `latest_seq`、点击先标记再跳到服务端链接、加载更早。门户与 Docs 的 `PlatformNav`、账户外壳的 `Topbar`（含手机抽屉）都挂它；偏好入口指向账户页 `#notifications`。
* 账户页「通知」节：类别表（站内显示 / 邮件方式）、安全行锁定为始终开启、免打扰（开始/结束/时区）、邮件预览详细度。三语言消息在 `notifications.messages.ts`。
* 角色门：应用与门户角色默认只放行少数账户路径（`http.py:APP_ACCOUNT_PATHS`）；五条收件箱路径在列，其余账户路由仍只在账户服务上。

## 6. 验收证据

`services/identity/tests/test_notifications.py`（6 项）：读者语言渲染与链接、他人不可读/不可标记、去重、`read-all` 游标语义、偏好关类别不落行但安全例外、偏好校验（未知类别/时间/时区/预览）、路由层：批量邀请 → 受邀者的待处理项（英文读者读英文）→ 标记已读不接受邀请 → 改口令产生 critical 行；调度器：恰好一次、汇总成一封、免打扰推迟普通通知而安全通知照发；邮件未配置时 skipped 而非丢失。浏览器与三外壳的实跑记录见 `docs/quality/verification.md` §16.22。

## 7. 明确未做

* Web Push：已在 CI-32 接入（`docs/platform/web-push.md`：VAPID 密钥、订阅表、每通知一行的推送队列、派发器推送趟、账户页设备管理）；真实设备上的收到与点击待人工验证。SMS 与第三方 IM 无业务需求不接。
* Docs 侧其它事件（模板或映射变更、产物即将失效）：随对应功能接入；文件被拒与任务结束（CI-29）、评论与提及（CI-33）已接入。
* 链接来源（CI-31 修正）：`docs` 目标的链接取应用注册表里 Docs 进程登记的启动地址；账户服务的 `SYNWEVO_APP_URL` 是它自己的地址，曾让邮件与账户外壳铃铛里的 Docs 链接指向 `accounts…/p/<id>`。只有 Docs 自己的进程才回退到 `SYNWEVO_APP_URL`；两者都没有时不给链接。
* 每会话静音、仅提及：随消息功能接入。
* 管理端不新增"通知仪表盘"；投递可在邮件管理页按 kind=`notification` 追踪。

## 版本化摘要预算（Admin B3，2026-09-30，仅本地）

`PlatformSettings`由唯一Store保存完整覆盖快照。发布、恢复默认和历史回滚都追加新版本；派发无进程缓存，在每收件人写事务中读取当前版本。`defer_reason`区分首次聚合、预算、免打扰与重试，只有旧版本预算等待能提前复核。旧投递迁移保持原`not_before`和未知版本NULL，不虚构执行确认。冻结/删除组织、当前成员资格、未读/静音/撤回均在入队前重新检查。

配置界面与使用步骤见[管理员说明](admin-operations.md#配置与发布边界)；本批证据见[B3记录](../quality/admin-b3-settings-20260930.md)。私信邮件默认关闭、主动开启、无逐条邮件规则保持不变。


## 通知入队失败的受控恢复（2026-10-08，整合验证中）

邮件管理中的“通知入队失败”仅显示通知编号、收件账户编号、类别、累计次数、更新时间与规范化错误。不返回通知正文、参数、业务目标或旧服务商错误原文。`platform.mail.view` 可查看；`platform.mail.manage` 经签名预览、理由、近期管理员重认证才能恢复排队。预览固定操作者、来源、对象和状态版本；状态变化或预览过期后必须重新预览。同一幂等键重复执行返回原回执，变参拒绝；变更、审计和持久回执在同一事务提交。

恢复仅适用于 email / failed / attempts≥5 / mail_id为空 / 精确错误码 `notification_enqueue_failed`，且投递从创建时即带 `enqueue_protocol=atomic-v1`。直接存在相同通知引用的邮件也会阻止恢复。迁移仅增加可空来源列和邮件引用索引；所有历史行保持NULL，不根据错误码、更新时间或Git发布日期猜测其来源，后续派发也不会将历史行升级为可重试。旧摘要中仅首条通知被存为邮件引用，因此“未查到邮件”本身不证明历史通知从未入队。

确认后只将原投递置回pending、安排一次当前资格检查，保留通知编号、去重键和累计尝试次数，清除旧的等待原因/预算版本。派发器仍检查账户、当前邮件偏好、已读/归档、会话成员与静音/未读、免打扰和摘要预算。等待不消耗尝试；再次入队失败立即回到failed，成功则关联实际outbox邮件。回执表示“已安排恢复”，不表示已寄出或收件箱收到。

不处理push重发、SMTP结果不明或已经关联邮件的重投，也不自动恢复来源不明的旧失败。滚动部署可与当前25b084f原子派发器共存：旧生产者创建的行不带标记，保持不可恢复；新行由两版原子派发器处理均不会出现部分入队。回退仅到包含原子派发修复8552091的版本（如25b084f）；保留新列与原业务记录，不回滚资金或通知事实。

接口：`GET /api/admin/notification-recovery` 按通知/账户编号查询并分页；`POST /api/admin/notification-recovery/{id}/preview`；`POST /api/admin/notification-recovery/{id}/execute` 接受 `preview_token`、理由和幂等键。当前实现及验证状态以 `docs/agent-work/STATE.md` 为准，本文不代表已部署。
