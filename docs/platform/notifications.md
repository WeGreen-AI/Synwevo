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
| `notification_preferences` | `categories` JSON `{类别: {in_app, email}}`、`quiet_start/quiet_end/timezone`、`preview` | 缺省：全部站内开、邮件立即；docs 与 conversation 缺省汇总 |

链接（`target`）只有 `origin ∈ {accounts, portal, docs}` 与以 `/` 开头的路径，由服务端根据本部署的 `SYNWEVO_ISSUER`/`PORTAL_URL`/`APP_URL` 拼成绝对地址；客户端从不拼接。

## 3. 事件目录（本轮接入）

| kind | 类别 / 严重度 | 接收者 | 触发点 | 邮件缺省 | 点击目标 |
|---|---|---|---|---|---|
| `security.change{what}` | security / critical | 账户持有人 | 改口令、重置口令、改邮箱、Passkey 增删、两步验证开关、恢复码重生成、外部登录关联/解除 | 立即，不可关 | 账户页 → 登录与安全 |
| `invitation.received` | organization / action | 受邀者（已有账户） | 发出/重发邀请（`dedupe_key=invite:{id}`，待处理） | 立即 | 引导页（列出等待接受的邀请，链接仍在邮件里） |
| `invitation.accepted` / `invitation.declined` | organization / info | 邀请人 | 受邀者接受 / 拒绝；同时把受邀者的待办置为 done / void | 汇总 | 空间成员页 |
| `invitation.revoked` | organization / info | 受邀者（已有账户） | 邀请被撤回；待办置为 void | 不发 | 引导页 |
| `member.role_changed` | organization / info | 当事成员 | 空间角色变化（升降都通知） | 立即 | 账户页 → 工作空间 |
| `member.removed` | organization / info | 当事成员 | 被移出空间 / 被移出组织 | 立即 | 账户页 |
| `member.left` | organization / info | 空间的 owner/admin | 成员自行退出 | 汇总 | 空间成员页 |
| `organization.role_changed` | organization / info | 当事成员 | 组织角色变化 | 立即 | 组织页 |
| `docs.file_rejected`、`docs.job_finished` | docs | 上传者 / 任务发起人 | **目录已定义，Docs 侧的发射点在下一批接入**（需要任务与来源行携带发起人） | 立即 / 汇总 | Docs |
| `member_import.finished` | organization / info | 发起导入的人 | 一次批量导入结束（完成/部分完成/失败/取消），附邀请/更新/跳过/失败计数 | 汇总 | 组织页 → 批量导入成员（`?import=<id>` 打开该批次） |
| `member_import.roles_applied` | organization / info | 接受邀请的新成员 | 导入指定的组织自定义角色在其接受邀请时由导入者身份分配成功 | 汇总 | 组织页 |

不发的：普通登录成功、任务进度百分比、每次路由切换——目录里没有这些 kind，就不会有人凑出通知。审批、成员导入、消息三个类别随各自功能接入。

## 4. 邮件投递（`synwevo_identity/notify.py`）

账户服务的 `NotificationDispatcher` 每 30 秒取 `pending` 且到期的投递：读者不存在/已禁用 → skipped；读者在免打扰时段（按其时区，安全通知除外）→ `not_before` 推到时段结束；否则**先在事务里把状态翻成 `queued`**（两个账户进程不会重复寄），再按读者语言渲染并 `Mailer.enqueue`（kind=`notification`，与邀请邮件同一个 outbox、同样的重试与管理页）。同一读者同一批里的多条普通通知合成一封汇总；`critical` 单独立即寄。邮件未配置 → skipped（原因可见）；入队失败 → 退避重试 5/15/60/240 分钟后 failed。`digest` 偏好只是把 `not_before` 推后 30 分钟，让同期事件落到同一批。

## 5. 界面

* `@synwevo/auth-client` 的 `NotificationBell`：铃铛 + 未读数（打开时、每 60 秒可见时、切回标签页时刷新，无长连接），抽屉列表三档筛选、逐条已读/未读/归档、「全部标为已读」只到打开列表时的 `latest_seq`、点击先标记再跳到服务端链接、加载更早。门户与 Docs 的 `PlatformNav`、账户外壳的 `Topbar`（含手机抽屉）都挂它；偏好入口指向账户页 `#notifications`。
* 账户页「通知」节：类别表（站内显示 / 邮件方式）、安全行锁定为始终开启、免打扰（开始/结束/时区）、邮件预览详细度。三语言消息在 `notifications.messages.ts`。
* 角色门：应用与门户角色默认只放行少数账户路径（`http.py:APP_ACCOUNT_PATHS`）；五条收件箱路径在列，其余账户路由仍只在账户服务上。

## 6. 验收证据

`services/identity/tests/test_notifications.py`（6 项）：读者语言渲染与链接、他人不可读/不可标记、去重、`read-all` 游标语义、偏好关类别不落行但安全例外、偏好校验（未知类别/时间/时区/预览）、路由层：批量邀请 → 受邀者的待处理项（英文读者读英文）→ 标记已读不接受邀请 → 改口令产生 critical 行；调度器：恰好一次、汇总成一封、免打扰推迟普通通知而安全通知照发；邮件未配置时 skipped 而非丢失。浏览器与三外壳的实跑记录见 `docs/quality/verification.md` §16.22。

## 7. 明确未做

* Web Push / Service Worker（需 VAPID 密钥、订阅表、`pywebpush` 依赖与真机验证）；SMS 与第三方 IM 无业务需求不接。
* Docs 侧发射点（文件被拒、任务完成）：目录已定义，等 Docs 的任务/来源行带上发起人后接入。
* 每会话静音、仅提及：随消息功能接入。
* 管理端不新增"通知仪表盘"；投递可在邮件管理页按 kind=`notification` 追踪。
