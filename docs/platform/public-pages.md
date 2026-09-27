# 门户公开页：首页、数据与隐私、服务说明

> 状态：2026-09-28 实现（round 9 · 公开页批次）。本文写的是**当前代码的行为与页面上每一句话的出处**；
> 页面文案改动必须同时改这里的表，三种语言一起改。运营方尚未决定的事项在 §5，页面上一律不写、不留占位。

## 1. 页面结构

| 页面 | 路径（zh-Hans 为无前缀的规范路径） | 前端 | 服务端 |
|---|---|---|---|
| 首页 | `/`、`/zh-Hant`、`/en`（别名 `/zh-Hans`） | `apps/portal/web/src/PublicHome.tsx` | `apps/portal/synwevo_portal/public.py` `COPY[locale]["pages"]["home"]` |
| 数据与隐私 | `/privacy`、`/zh-Hant/privacy`、`/en/privacy` | `apps/portal/web/src/PublicPages.tsx` `privacyContent()` | 同上 `"privacy"` |
| 服务说明 | `/service`、`/zh-Hant/service`、`/en/service` | `PublicPages.tsx` `serviceContent()` | 同上 `"service"` |
| 运行时事实 | `/public/deployment.json` | `PublicPages.tsx` `useDeploymentFacts()` | `public.py` `deployment_facts()` |

* 路径表在两处各写一份并保持一致：`public.py` 的 `PUBLIC_PATHS` / `PUBLIC_PAGES` / `public_path()` 与
  `apps/portal/web/src/publicRoutes.ts` 的 `publicRoute()` / `publicPath()`。`main.tsx` 从路径的第一段取语言。
* 服务端渲染（`document()`）：`<html lang>`、`<title>`、`meta description`、`robots index,follow`、
  `canonical` 与三语言 + `x-default` 的 `hreflang`（只在配置了合法 `SYNWEVO_PORTAL_URL` 时），`#root` 里的无脚本回退
  = 标题 + 描述 + 摘要段落 + 注册 / 登录 / 其它两页的链接（回退里永远没有 `/workspace`）。
* 分享摘要（CI-80）：公开页的服务端 `<head>` 带 `og:site_name/type/title/description/locale`（zh_CN / zh_HK / en_US 及另两种为 alternate）与
  `twitter:card=summary_large_image`；`og:url`、`og:image`（`/share-card.png`，1200×630，由确认 Logo 生成）只在配置了合法公开源时出现，从不取自 Host；
  私有页（工作空间、登录等）没有任何 `og:` 标签。
* `robots.txt` 对全部 12 个公开路径 `Allow: <path>$`；`sitemap.xml` 列 9 个规范路径（3 页 × 3 语言，`/zh-Hans/...` 别名不列）。
* 首页叙事顺序：它是什么（主视觉 + 品牌卡）→ 今天可用的应用（Synwevo Docs、Synwevo Message，没有第三张卡）→
  一起怎样完成工作（工作空间 / 组织 / 角色与授权 / 通知）→ 为什么可以信任（数据与隐私摘要，链接到全文）→ 下一步。
* 数据与隐私 / 服务说明的分层：页首（眉标、标题、导语、「不是正式法律条款」提示）→ 摘要卡 → 「本部署的当前状态」
  （§3）→ 编号目录 → 编号章节 → 页脚（去处：账户页 / 组织管理员；返回首页 / 另一页）。
* CTA 三态沿用 `PublicHome` 的 `signedIn`（`undefined` 时不显示登录 / 进入；`false` → 真实注册 `accountLink(account, "/register")`
  与 `/auth/start?next=%2Fworkspace`；`true` → `/workspace`、消息卡 → 账户源站 `/messages`）。所有跨源地址来自
  `useSession().state.account_url` 或服务端 `public_origin()`，页面里没有任何写死的域名。
* 品牌：三种官方 Logo 只经 `@synwevo/ui` 的 `Brand` / `Logo` 使用；品牌卡用字标版，页脚用字标版，顶栏用完整版。
  `WeaveMotif` 是抽象的「汇聚织线」装饰（内联 SVG、`currentColor`），静态，不是产品截图，也不是动画。
* 词汇：工作空间、组织、文档、消息、通知；zh-Hant 沿用目录既有选择（資料 / 私隱 / 工作空間 / 訊息 / 檢舉 / 密碼 / 私訊）。

## 2. 事实 → 出处

每一条都在写入页面前读过所指的代码。「页面」列：H = 首页，P = 数据与隐私，S = 服务说明。

### 2.1 产品与应用

| 陈述 | 页面 | 出处 |
|---|---|---|
| 平台 = 统一账户、工作空间、组织、通知 + 面向自动化集成的接口平台；应用只有 Synwevo Docs 与 Synwevo Message；没有其它应用或编排器 | H S | `AGENTS.md`（产品定位）；`packages/core/synwevo_core/apps.py:341` `bootstrap_docs_app`（注册表里唯一的应用）；`docs/platform/messaging.md` |
| Synwevo Docs：选择来源数据、接入模板、检查后批量生成；DOCX / XLSX / PDF / PPTX / CSV；各格式能力以应用内为准 | H S | 沿用原首页文案（`apps/portal/web/src/messages.ts` 旧键，见 §6）；`apps/docs/` |
| Synwevo Message：私聊与群组、引用回复、@提及、表情回应、附件、文档链接卡片、群公告、举报处理；目前从平台栏「消息」进入 | H S | `docs/platform/messaging.md:12-14, 41, 60-62`；`services/identity/web/src/main.tsx:109-110`（`/messages` 路由）；`packages/auth-client/web/src/MessagesLink.tsx`；「正在成为独立应用」= round-9 brief 的规划陈述，页面只说「目前从…进入」 |
| 新成员只看到加入之后的消息；退出或被移除后不再可见；重新加入不恢复 | H P | `docs/platform/messaging.md:28`；`packages/core/synwevo_core/messaging.py`（`joined_seq`、`left_seq`） |
| 没有在线状态、他人已读回执、正在输入提示 | H P | 研究清单 `inventory:messaging` GAPS；`docs/platform/messaging.md:14`「不做：在线状态、他人已读、端到端加密」 |
| 没有任何 AI 处理用户内容 | H P | 仓库内无任何模型调用（`grep -ri "openai\|anthropic\|llm" packages apps services` 只命中文档 / 组件名守卫的说明） |

### 2.2 角色与授权

| 陈述 | 页面 | 出处 |
|---|---|---|
| 平台管理员治理账户与配置，读不到业务文档与对话 | H P S | `AGENTS.md` §数据与安全；`tests/integration/test_portal_public.py::test_empty_account_and_platform_admin_do_not_inherit_business_access` |
| 接口平台控制台只对获授权的平台管理员开放；组织 / 空间管理员不因此获得 | S | `CLAUDE.md`「The API console is platform-admin-only」；`apps/portal/web/src/Portal.tsx`（`canManageApi` 需 `api_platform.manage`） |
| 服务凭据属于工作空间、须经批准、受资源范围与权限范围交集限制；管理权不授予内容访问 | H P S | `AGENTS.md` §数据与安全；`packages/core/synwevo_core/http.py:1001-1016`（`/api/v1` 凭据校验与租户绑定） |
| 组织角色：所有者 / 管理员 / 成员 + 按能力委派的自定义角色 | S | `docs/platform/organization-roles.md`（CI-23）；`packages/core/synwevo_core/org_roles.py` |
| 组织成员身份不自动授予每个工作空间的访问权 | H S | `test_portal_public.py::test_organization_visibility_never_implies_workspace_or_app_access` |
| 工作空间角色：所有者 / 管理员 / 编辑者 / 查看者；权限按空间授予，切换不合并 | H S | `Portal.tsx` `role()`；`packages/core/synwevo_core/http.py:802-806`（每次请求重查成员关系） |
| 举报处理只看到被举报消息的快照、举报人与原因，看不到会话其它内容；需重新验证身份 | H P | `docs/platform/messaging.md:60-62`；`services/identity/synwevo_identity/messaging_routes.py:309, 323-328` |
| 被移出组织即刻失去该组织的会话与工作空间访问 | S | `packages/core/synwevo_core/organizations.py:431-432` → `Messaging.withdraw_from_organization`（`messaging.py:462`）；`http.py:802-806` |

### 2.3 注册、登录、会话

| 陈述 | 页面 | 出处 |
|---|---|---|
| 注册策略 open / invite / closed，实时读取 | S | `packages/core/synwevo_core/identity.py:851-860`；`packages/auth-client/python/synwevo_auth_client/session_routes.py:42`（`registration`） |
| 开放时任何邮箱验证后即可使用；机构邮箱同样可用 | S | `services/identity/synwevo_identity/registration.py:66`；`docs/operations.md` §6.1 机构邮箱投递；`services/identity/scripts/ui_e2e_institutional_mail.py` |
| 验证链接 24 小时 | P S | `services/identity/synwevo_identity/invites.py:32` `VERIFY_TTL_S`；`registration.py:34` |
| 注册限频：同一来源 15 分钟 10 次；同一邮箱每小时 3 次 | S | `registration.py:56, 61` |
| 登录方式：口令、通行密钥、邮箱验证码；外部账户登录按配置 | S | `services/identity/synwevo_identity/auth_routes.py:277`；`passkeys.py`；`email_login.py:83-90`；`external.py:68-69, 171` |
| 两步验证与恢复码 | P S | `auth_routes.py`（`/totp/*`、`/recovery/*`）；`docs/design.md` §4 账户中心 |
| 会话：闲置 12 小时；「保持登录」14 天；最长 30 天；敏感操作重新输入口令（10 分钟窗口） | P S | `packages/core/synwevo_core/sessions.py:43, 44, 48, 51` |
| 外部登录只取邮箱与显示名 | P | `external.py:316-321`（`email`、`email_verified`、`name` → `display_name`） |
| 人机验证：启用时在登录 / 注册等动作前判断是否自动程序；站点密钥公开、密钥不公开 | P | `packages/core/synwevo_core/challenge.py:77-85`；`session_routes.py:150-153` |
| 「已发送」= 邮件服务器已接收；能否入箱取决于对方系统 | P S | `packages/core/synwevo_core/store.py:417-421` `mail_outbox` 注释；`infra/production/README.md:61-62`；研究清单「Mail deliverability honesty」 |

### 2.4 存储、传输、备份、第三方

| 陈述 | 页面 | 出处 |
|---|---|---|
| 单台服务器，位于日本东京；数据库与文件都在这台机器上 | H P S | `infra/production/README.md:1-4, 30-34`（Vultr Tokyo；三个角色共用一个数据库与 blob 目录） |
| HTTPS；公开域名由边缘网络服务商终止 TLS，经隧道到达源站 | P | `infra/production/README.md:22`（Cloudflare Tunnel supplies public HTTPS） |
| 服务器端明文存储，没有端到端加密 | H P | `docs/platform/messaging.md:14`；研究清单「End-to-end encryption: Not implemented; server stores plaintext bodies」 |
| 上传文件先经大小与格式检查；恶意软件扫描是部署选项 | P | `packages/core/synwevo_core/malware.py:1-25`（策略 `required` / `off`）；`member_import.py:71`；`messaging.py:89, 731` |
| 每次发布前完整备份，保留在同一服务器，不自动清理，含全部数据与密钥材料 | H P S | `infra/production/README.md:33-34, 107-119`（pre-upgrade backup；`/opt/synwevo/backups` 含真实数据与密钥；Backups are not pruned automatically） |
| 发布可能有短暂中断；失败以备份回滚 | S | `infra/production/README.md:107-119` |
| 生产页面注入边缘网络服务商的网站分析脚本；它不用 Cookie / 浏览器存储、在最近节点丢弃 IP、不做指纹识别；收集页面浏览与加载性能 | H P | `packages/core/synwevo_core/http.py:61-69` `CLOUDFLARE_CSP`（只在请求带 `CF-Ray` 时放行 `static.cloudflareinsights.com`）；供应商说明 developers.cloudflare.com/web-analytics（About；「RUM beacon for Web Analytics」§Data collection：不访问 cookies / localStorage / sessionStorage / IndexedDB，在最近数据中心丢弃 IP；Core Web Vitals §Information collected：不做指纹识别），2026-09-28 读取 |
| 没有其它第三方脚本 | P | `http.py` `DEFAULT_CSP` `script-src 'self'`；`tests/integration/test_security_headers.py:48-50` |
| 系统邮件由自行运营的邮件服务器发出，经第三方投递服务中转 | P | `infra/production/README.md:61-62`（mailcow → SMTP2GO） |
| 浏览器推送：订阅 = 推送地址 + 两个密钥，只显示哈希；绑定会话，退出即撤销；负载加密，只含标题 / 正文 / 链接 | P | `packages/core/synwevo_core/push.py:1-15` |

### 2.5 Cookie 与浏览器存储

| 陈述 | 页面 | 出处 |
|---|---|---|
| 四个 Cookie 都只发给本站域名（host-only，无 `Domain`），`SameSite=Lax`，`Path=/` | P | `sessions.py:349-361` `cookie_kwargs`；`AGENTS.md`「不要用扩大 Cookie 作用域…」 |
| `dw_session` HttpOnly；期限同会话 | P | `sessions.py:29`；`packages/auth-client/python/synwevo_auth_client/routes.py:130` |
| `dw_csrf` 页面可读、随修改请求回传 | P | `sessions.py:30`；`routes.py:131-132`（`httponly: False`）；`packages/auth-client/web/src/api.ts:195-215` |
| `dw_authreq` 登录往返，10 分钟 | P | `routes.py:28, 75`（`max_age=600`） |
| `dw_extreq` 外部登录往返 | P | `services/identity/synwevo_identity/external.py:63, 474`（`REQUEST_TTL_S`，页面未写具体时长） |
| localStorage：界面语言、界面布局偏好、消息草稿、Enter 键偏好、退出标记 | P | `packages/ui/src/i18n-core.ts:8` `synwevo.locale`；`packages/ui/src/theme.ts:9-53`（`synwevo.*` 布局偏好）；`services/identity/web/src/pages/Messages.tsx`（`draftKey`、`synwevo.msg.enter`）；`packages/auth-client/web/src/session.tsx:248` `synwevo.signed-out` |
| sessionStorage：当前工作空间、返回目标、登录流程状态 | P | `packages/auth-client/web/src/api.ts:176` `synwevo.tab-workspace`；`services/identity/web/src/lib/returnTarget.ts:15`；`services/identity/web/src/pages/org/AuthFlow.tsx:10` |

### 2.6 保留期限

| 陈述 | 页面 | 出处 |
|---|---|---|
| 账户删除缓冲 7 天 | H P S | `services/identity/synwevo_identity/profile.py:26` `GRACE_SECONDS`；每小时执行 `finalize_deletions`（`app.py:47-54`） |
| 会话记录结束后 7 天内清理 | P | `sessions.py:276` `sweep(older_than_s=7 天)`，`services/identity/synwevo_identity/app.py:23, 47-54`（每小时） |
| 登录 / 注册频率计数 24 小时 | P | `packages/core/synwevo_core/identity.py:39, 317-336` |
| 邀请默认 7 天，可设 1–30 天 | P S | `invites.py:25-27` |
| 组织删除 7 天可恢复 | P S | `packages/core/synwevo_core/organizations.py:20, 484-500` |
| 接口调用元数据 30 天 | P | `packages/core/synwevo_core/api_platform.py:253-254` |
| 成员导入记录标注 7 天有效期；**到期清理没有自动任务** | P | `member_import.py:72, 389, 680-685`（`sweep_expired` 无调用方：`grep -rn sweep_expired` 只命中定义） |
| 安全活动记录（含 IP）、邮件投递记录、通知：长期保留，无自动清理 | P | `grep -rn "DELETE FROM audit_events"` 无结果；`store.py:417-424` `mail_outbox`；`notifications.py` 无清理 |
| 删除的消息对他人显示「消息已删除」，正文清空、序号保留 | P | `docs/platform/messaging.md:14, 28` |
| 删除的文档项目标记为已删除；底层文件不自动回收 | P | 研究清单「Docs artifacts/blobs (soft delete only, blobs never garbage-collected)」；`packages/core/synwevo_core/journal.py:108-110` |
| **未写进页面**：「未使用的附件 1 小时后删除」 | — | `messaging.py:754` `sweep_unused_attachments` 没有调度器调用（研究清单「Attachment sweep scheduler … never executed」），所以不声称 |

### 2.7 导出与删除

| 陈述 | 页面 | 出处 |
|---|---|---|
| 导出在账户页「数据与账户」，需当前口令；JSON 含资料、工作空间与组织成员关系、通行密钥名称、安全活动、头像；不含消息与文档 | P | `services/identity/synwevo_identity/account_routes.py:229-247`；`profile.py:34-38` `public_profile`；`services/identity/web/src/pages/account/PrivacySection.tsx:27`（`#privacy`） |
| 删除：需当前口令；立即退出全部登录、撤销待接受邀请、进入 7 天缓冲；邮件链接可撤回 | P S | `account_routes.py:249-275`（`invalidate_access`、撤销 invitations、`account_recoveries`） |
| 到期后删除口令、通行密钥、两步验证、恢复码；清空资料与邮箱；账户标识与邮箱占位保留；组织内贡献保留 | P S | `profile.py:176-197` `finalize_deletions`（`email_key remains reserved`；`preserve immutable user IDs and organization contributions`） |
| 前提：唯一组织所有者或最后一位平台管理员须先移交 | P S | `profile.py:142-156` `check_deletion_constraints` |
| 组织所有者可冻结 / 删除 / 7 天内恢复组织 | S | `organizations.py:484-500` `transition`（`require(owner=True)`） |
| 平台管理员可停用账户，停用即刻结束全部登录 | S | `services/identity/synwevo_identity/admin_routes.py:424-445` |

### 2.8 限额（服务说明表）

| 项目 | 数值 | 出处 |
|---|---|---|
| 消息正文 | 4000 字 | `messaging.py:85` |
| 每人每分钟消息 | 60 | `messaging_routes.py:36, 274` |
| 附件 | 每条 1 个，20 MB；每人每小时 30 个 | `messaging.py:89, 731`；`messaging_routes.py:37, 372` |
| 群成员 | 200 | `messaging.py:86` |
| 每人每天建群 | 20 | `messaging_routes.py:38, 156` |
| 群公告 | 1000 字 | `messaging.py:82` |
| 举报 | 每人每天 20 | `messaging_routes.py:39, 309` |
| 邀请 | 每空间 24 小时 50 个；同一邮箱 60 秒冷却；默认 7 天（1–30） | `invites.py:31, 101-102, 110-118, 25-27` |
| 注册 | 每来源 15 分钟 10 次；每邮箱每小时 3 次 | `registration.py:56, 61` |
| 成员导入文件 | 20 MB | `member_import.py:71` |

## 3. 运行时读取的事实（`/public/deployment.json`）

`public.py` `deployment_facts()` 在服务端读取（浏览器只访问同源文档，CSP `connect-src 'self'` 不变）：

| 字段 | 来源 | 页面文字 |
|---|---|---|
| `accounts.malware_scanner` | 账户服务 `{issuer}/readyz` → `checks.malware_scanner`（`infra/server/synwevo_server/app.py:46-55`；`malware.py:178-187`：`ok` / `off` / `down: …`） | 已启用 / 未启用 / 已配置，当前不可用 / 暂时无法读取 |
| `docs.malware_scanner` | 注册表里 Synwevo Docs 的 `launch_url` 源站 `/readyz`（`apps/docs/synwevo_docs/api/app.py:873`） | 同上 |
| `accounts.human_verification` | `{issuer}/api/auth/state` → `challenge`（`session_routes.py:150-153`） | 已启用 / 未启用 |
| `accounts.external_providers` | 同上 → `external_providers[].label`（`external.py:171`） | 提供商名列表 / 未启用 |
| `accounts.registration` | 同上 → `registration`（`open` / `invite` / `closed`） | 开放注册：邮箱验证后即可使用 / 仅凭邀请注册 / 已关闭 |
| `accounts.mail_sender` | 同上 → `mail_sender`（`null` = 未配置） | `Name <address>` / 未配置 |

规则：只访问配置好的源站（`SYNWEVO_ISSUER` 与应用注册表），不用请求里的任何值；结果缓存 60 s、单次超时 3 s；
任何读不到的项为 `null`，页面显示「暂时无法读取」而不是默认值；文档不进索引（非公开路径自动带 `X-Robots-Tag: noindex`）。
为什么不在浏览器直接读账户源站：跨源无 CORS；也不能在门户进程里读 `malware.policy()`——生产里门户进程没有扫描器配置，
会把「未启用」说成事实。

## 4. 页面 / 旧问题 / 新表达 / 理由（代表性示例）

| 页面 | 旧问题 | 新表达 | 理由 |
|---|---|---|---|
| 首页主视觉 | 「把重复的工作，交给流程。」+「连接团队、应用与数据，让重复工作成为清晰、可追踪的自动化流程。」—— 只讲流程，与品牌无关 | 「让工作，自然相连。」+「文档自动化、团队协作与消息沟通，汇聚于同一个工作空间。」 | 先说它是什么（三件事汇于一处），品牌意义留给品牌卡 |
| 首页右栏 | 「工作流程示意」三阶段卡（输入与规则 → 应用处理 → 任务与结果），一张不对应任何真实界面的示意图 | 品牌卡：字标 + 「名字的由来」+ 一段协同与编织的文案 + 静态织线装饰 | 不放假界面；把品牌含义织进文案而不是拆词讲解 |
| 首页能力条 | 「让每一步，都有清晰的归属 · 统一账户 · 团队空间 · 应用授权 · 可追踪任务 · 受控集成」 | 「同一个入口 · 统一账户 · 工作空间 · 组织与角色 · 文档 · 消息 · 通知」 | 统一词汇；条目都是页面下文真正解释的概念 |
| 首页「工作流程」三步 | 「从同一个空间开始 / 把重复步骤交给应用 / 让结果进入下一步」—— 泛泛的流程口号，与任何应用无关 | 删去；改为「今天可用的应用」两张卡 + 「一起完成工作」四栏 | 叙事顺序：应用 → 协作，不写没有的编排器 |
| 首页应用区 | 「一个平台，应用各司其职。」只有 Docs 一张卡；消息应用不存在于页面 | 「两个应用，各司其职。」Docs + Message 两张卡，并明说「此外没有别的应用」 | 消息已在生产（`/messages`），首页却不提；同时不虚构第三个应用 |
| 首页「数据与隐私」 | 三张原则卡：「身份统一，空间独立 / 自动化遵循授权 / 管理不等于读取」—— 只有原则，没有一条可核对的事实 | 四条摘要（导出与删除 7 天、HTTPS 与无端到端加密、无 AI / 无在线状态、Cookie 与东京单机与备份）+ 到 `/privacy` `/service` 的链接 | 每条都能在本文 §2 找到出处；全文在独立页 |
| 首页「服务说明」 | 「从一个工作空间开始。创建账户，选择个人使用或加入团队，再打开适合当前任务的应用。你的账户、空间和应用权限在平台中统一管理。」+「业务数据保存在当前部署中。数据保留、备份与可用能力由实际部署配置决定。」—— 注册推销 + 一句含糊的免责 | 「下一步」：一句话说明注册与邀请如何生效；服务说明成为独立页 | 免责句被真实的保留期限表与运行时状态取代 |
| 页脚 | `/#privacy`、`/#service` 锚点，「让工作流程，持续运转。」 | `/privacy`、`/service`（按当前语言加前缀），「协同 · 编织 · 持续完善」 | 链接到真实页面；页脚一句呼应品牌而不重复主标题 |
| 门户仪表盘 | 「只显示你已加入的空间。」「切换空间不会合并内容。」「请联系空间管理员…」「个人空间 / 团队空间」 | 统一为「工作空间」 | 词汇统一 |
| 门户仪表盘 | 「这些邀请尚未授予你空间访问权。请通过邀请邮件中的链接确认加入，账户页可核对邀请状态。」 | 「邀请通过邮件中的链接确认后才生效；账户页可核对状态。」 | 去掉「点击…」式的操作说明，保留结果与去处 |
| 门户仪表盘 | 「当前没有可显示的待接受邀请。你也可以到账户页核对邮箱和邀请状态。」 | 「当前没有待接受的邀请。」 | 去重复 |
| 未登录状态页 | 「登录你的统一账户，继续你的任务与团队工作。」 | 「登录统一账户，继续你的工作。」 | 精简 |
| 目录死键 | 88 条旧键（Docs 三步、示意卡、旧原则卡等）无人引用 | 删除 | 三语言目录只保留在用的键（`portal_keys.py` 校验：275 用 / 274 键，差额 2 条为共享目录键 `退出登录` 与模板字面量） |

## 5. 运营方待决事项（页面上不写、不留占位）

1. **运营主体**：法律实体名称、注册地址。
2. **联系渠道**：数据保护 / 隐私联系邮箱或表单；目前产品里只有账户页请求与「联系组织管理员」。
   邮件发件域（`hikosphere.com`）与产品域（`wegreen.ltd` → `synwevo.com`）不一致，也待决定。
3. **管辖与适用法**：香港 PDPO / GDPR / PIPL 的适用性、法律依据、跨境传输措辞（用户含香港机构；数据在东京；边缘网络全球）。
4. **正式条款**：服务条款 / 可接受使用政策的版本号、生效日期、同意机制（注册时勾选或继续即同意）；现在没有任何条款文本，页面明确写「不是正式法律条款」。
5. **年龄与资格限制**：目前注册不做年龄或身份检查。
6. **费用、退款、可用性承诺（SLA）**：目前没有收费，也没有可用性指标；页面不写。
7. **是否在页面上点名第三方处理方**（边缘网络与分析：Cloudflare；主机：Vultr 东京；邮件：mailcow → SMTP2GO；推送：各浏览器厂商）：
   `scripts/check_component_names.py` 不允许自有界面文字出现 Cloudflare 等组件名，页面因此写「边缘网络服务商」等能力名并在本文列出名称；
   若要点名，需在该守卫 `STABLE` 里登记例外并写理由（法律披露），或改为运营方另行公布的处理方清单。
8. **是否关闭分析脚本**：Cloudflare Web Analytics 是 zone 级设置，代码只在 `CF-Ray` 请求上放行其 CSP；是否需要同意机制属法律评估。
9. **未实现的清理任务**：成员导入到期清理（`sweep_expired`）、未使用附件清理（`sweep_unused_attachments`）、
   审计事件 / 邮件投递记录 / 通知的保留期。页面如实写「没有自动任务 / 长期保留」；实现后改页面与 §2.6。
10. **删除的完整性**：删除后 `email_key` 与用户 id 永久保留、组织贡献保留（含审计行里的 IP）。页面如实披露；是否改为匿名化属产品决定。

## 6. 验证（2026-09-28，隔离实例，未触碰 `data/`）

| 检查 | 结果 |
|---|---|
| `npm run typecheck && npm run build`（三个外壳） | 通过 |
| `npm test --workspaces --if-present` | 通过（共享 UI、auth-client、Docs、账户外壳 19 项校验） |
| `scripts/check_design.py` / `check_brand.py` / `check_component_names.py` / `check_boundaries.py` / `brand_assets.py --check` | 全部通过 |
| 目录覆盖（每个 `t()` 字面量都有 en / zh-Hant，无死键） | 275 用 / 274 键；差额为共享目录的 `退出登录` 与一处模板字面量 |
| `tests/integration/test_portal_public.py` | 5 个用例按预期失败（旧文案与三条 sitemap 写死，见 §7），其余通过 |
| `tests/integration`（其余全部；在工作树用 `PYTHONPATH` 指向本工作树的包，跳过上述 2 个写死用例；需先 `apps/docs/scripts/make_fixtures.py` 生成 `testdata/`） | 593 通过，0 失败（退出码 0） |
| `apps/docs/tests`、`services/identity/tests` | 未运行：本批次只改 `apps/portal/**` 与两份文档，这两个套件不导入 `synwevo_portal` |
| 浏览器（Chrome + Playwright WebKit，经 `localhost_proxy`；1280 与 375；zh-Hans / zh-Hant / en；`/`、`/privacy`、`/service`） | 36 个页面结果全部通过：0 控制台错误（已登出访客的 `/api/auth/me` 401 是预期行为，单独判定）、0 CSP 违规、375 无横向溢出、无占位文本、`<title>` / `lang` / canonical 逐页逐语言正确、未登录顶栏为「登录」→ `/auth/start?next=%2Fworkspace&lang=…`（303 进入账户服务）、每个 CTA 与页脚链接可达、两页的扫描器状态文字渲染（隔离实例：未启用；注册策略：开放；发件人：Synwevo <synwevo@example.test>） |
| 已登录访客（夹具账户 `public-pages-fixture@example.test`，只在隔离数据目录） | 载入 `/` 时 `.pt-nav .pt-link` 只出现「进入工作空间」，从未出现「登录」；主 CTA → `/workspace`，消息卡 → 账户源站 `/messages`；`/privacy` 的摘要链接 → 账户页 `#privacy` |
| 截图 | `<worktree>/.scratch/shots/public-{chromium,webkit}-{1280,375}-{zh-Hans,zh-Hant,en}-{home,privacy,service}.png`、`public-signed-in-*.png`、`viewport-*.png` |

## 7. 已知限制与随附事项

* `tests/integration/test_portal_public.py` 把旧首页文案（标题 / 描述 / `<h1>`）与 sitemap 的三条 URL 写死；本批次不改测试文件（范围限定），
  五个用例按预期失败。新的期望值：标题不变；描述「文档自动化、团队协作与消息沟通，汇聚于同一个工作空间。」（zh-Hant「文件自動化、團隊協作與訊息溝通，匯聚於同一個工作空間。」，
  en「Document automation, team collaboration and messaging, brought together in one workspace.」）；`<h1>`「让工作，自然相连。」/「讓工作，自然相連。」/「Work that comes together.」；
  robots 多出 8 条 `Allow`；sitemap 为 9 条（`/`、`/zh-Hant`、`/en`、`/privacy`、`/zh-Hant/privacy`、`/en/privacy`、`/service`、`/zh-Hant/service`、`/en/service`）；
  `/privacy` 与 `/service` 三语言也应加入参数化用例。
* `apps/portal/IMPLEMENTATION.md` 的路由契约已同步（公开路径 12 条、`/public/deployment.json`）。
* 组件名守卫的结果：页面用「边缘网络服务商 / 第三方投递服务 / 浏览器厂商的推送服务」，名称在本文 §5.7。
