# 门户公开页：首页、数据与隐私、服务说明、服务条款、AI 使用规范、定价

> 状态：2026-09-28 实现（round 9 · 公开页批次）。本文写的是**当前代码的行为与页面上每一句话的出处**；
> 页面文案改动必须同时改这里的表，三种语言一起改。运营方尚未决定的事项在 §5，页面上一律不写、不留占位。

## 1. 页面结构

| 页面 | 路径（zh-Hans 为无前缀的规范路径） | 前端 | 服务端 |
|---|---|---|---|
| 首页 | `/`、`/zh-Hant`、`/en`（别名 `/zh-Hans`） | `apps/portal/web/src/PublicHome.tsx` | `apps/portal/synwevo_portal/public.py` `COPY[locale]["pages"]["home"]` |
| 数据与隐私 | `/privacy`、`/zh-Hant/privacy`、`/en/privacy` | `apps/portal/web/src/PublicPages.tsx` `privacyContent()` | 同上 `"privacy"` |
| 服务说明 | `/service`、`/zh-Hant/service`、`/en/service` | `PublicPages.tsx` `serviceContent()` | 同上 `"service"` |
| 服务条款 | `/terms`、`/zh-Hant/terms`、`/en/terms` | `PublicPages.tsx` `termsContent()` | 同上 `"terms"` |
| AI 使用规范 | `/ai-use`、`/zh-Hant/ai-use`、`/en/ai-use` | `PublicPages.tsx` `aiUseContent()` | 同上 `"ai-use"` |
| 定价 | `/pricing`、`/zh-Hant/pricing`、`/en/pricing` | `PublicPages.tsx` `pricingContent()` | 同上 `"pricing"` |
| 运行时事实 | `/public/deployment.json` | `PublicPages.tsx` `useDeploymentFacts()` | `public.py` `deployment_facts()` |

* 路径表在两处各写一份并保持一致：`public.py` 的 `PUBLIC_PATHS` / `PUBLIC_PAGES` / `public_path()` 与
  `apps/portal/web/src/publicRoutes.ts` 的 `publicRoute()` / `publicPath()`。`main.tsx` 从路径的第一段取语言。
* 服务端渲染（`document()`）：`<html lang>`、`<title>`、`meta description`、`robots index,follow`、
  `canonical` 与三语言 + `x-default` 的 `hreflang`（只在配置了合法 `SYNWEVO_PORTAL_URL` 时），`#root` 里的无脚本回退
  = 标题 + 描述 + 摘要段落 + 注册 / 登录 / 其它两页的链接（回退里永远没有 `/workspace`）。
* 分享摘要（CI-80）：公开页的服务端 `<head>` 带 `og:site_name/type/title/description/locale`（zh_CN / zh_HK / en_US 及另两种为 alternate）与
  `twitter:card=summary_large_image`；`og:url`、`og:image`（`/share-card.png`，1200×630，由确认 Logo 生成）只在配置了合法公开源时出现，从不取自 Host；
  私有页（工作空间、登录等）没有任何 `og:` 标签。
* `robots.txt` 对全部公开路径（6 页 × 3 语言 + `/zh-Hans/...` 别名）`Allow: <path>$`；`sitemap.xml` 列 18 个规范路径（6 页 × 3 语言，别名不列）。
* 带版本的文档（服务条款、AI 使用规范、定价，2026-10-05 起）：版本与生效日期只在 `packages/core/synwevo_core/terms.py`
  `DOCUMENTS` 定义；`public.py` 把它印进三语言首段（`_versioned`），外壳的 `publicRoutes.ts` `DOCUMENT_VERSIONS` 写同样的值，
  `tests/integration/test_public_ai_facts.py` 保持三者一致。这三页的页首写「版本 …，自 … 起生效」，不写「不是正式的法律条款」。
  改措辞即发新版本（当前：服务条款与定价 2026-10-24，AI 使用规范 2026-10-05）；新版本生效后，下一次使用 Synwevo Agent 时
  在「开始使用 Synwevo Agent」步骤中再接受一次并记录（`agent_terms_acceptances`）；单价变化不改文件版本。
* 首页叙事顺序：它是什么（主视觉 + 品牌卡）→ 今天可用的应用（Synwevo Docs、Synwevo Message，没有第三张卡）→
  一起怎样完成工作（工作空间 / 组织 / 角色与授权 / 通知）→ 为什么可以信任（数据与隐私摘要，链接到全文）→ 下一步。
* 数据与隐私 / 服务说明的分层：页首（眉标、标题、导语、「不是正式法律条款」提示）→ 摘要卡 → 「本部署的当前状态」
  （§3）→ 编号目录 → 编号章节 → 页脚（去处：账户页 / 组织管理员；返回首页 / 另一页）。
* CTA 三态沿用 `PublicHome` 的 `signedIn`（`undefined` 时不显示登录 / 进入；`false` → 真实注册 `accountLink(account, "/register")`
  与 `/auth/start?next=%2Fworkspace`；`true` → `/workspace`、消息卡 → `messagesHref(state)`，即注册表里 Synwevo Message 的地址经其登录入口；CI-88 前是账户源站 `/messages`）。所有跨源地址来自
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
| Synwevo Message：私聊与群组、引用回复、@提及、表情回应、附件与语音、文档链接卡片、群公告、举报处理；独立打开，也可以从工作空间顶栏「消息」进入 | H S | `docs/platform/messaging.md` §7；`apps/messages`（CI-66，2026-09-28 起在 message.synwevo.com）；`packages/auth-client/web/src/MessagesLink.tsx`（CI-88 改写；此前写「目前从平台栏进入、正在成为独立应用」，CI-66/CI-82 上线后失实） |
| 新成员只看到加入之后的消息；退出或被移除后不再可见；重新加入不恢复 | H P | `docs/platform/messaging.md:28`；`packages/core/synwevo_core/messaging.py`（`joined_seq`、`left_seq`） |
| 在线状态与正在输入只给同一会话的成员看、不保存、可关闭；已读回执默认关闭，双方都开启才显示 | H P | `docs/platform/messaging.md` §7.2–7.4；`packages/core/synwevo_core/messaging.py`（`messaging_settings` 缺省 `show_presence=1`、`read_receipts=0`，互惠）、`messaging_presence.py`（只在内存）。CI-88 前页面写「没有在线状态、他人已读回执或正在输入提示」——CI-74/CI-73 上线后失实 |
| 只有 Synwevo Agent 会把内容发给 AI 模型：在你使用它并单独同意后（首次使用的「开始使用 Synwevo Agent」步骤里单独勾选，不与条款捆绑），发送给模型提供方 DeepSeek；组织工作空间的内容默认不发送；其它应用不把内容交给 AI 模型（2026-10-05 取代「没有任何 AI 处理你的内容」——Agent 存在后该句失实；所有者决定公开点名 DeepSeek，不用其 Logo、不暗示合作） | H P | 模型调用只在 `apps/suite/synwevo_suite/agent/`（`engine.py` → `provider.py` / `deepseek.py` / `gateway.py`）；单独同意 `synwevo_core/agent_safety.py` `ProviderConsents.require`（`engine._check_person`，入列与每次模型调用前）；空间策略 `agent/tools.py` `workspace_policy`（组织空间无策略 = `none`） |
| Synwevo Agent 是否在本部署开放（实时） | P S（当前状态） | §3 `agent` |

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
| 成员导入记录与上传文件在上传 7 天后自动删除（正在执行的导入除外） | P | `member_import.py` `RETENTION_S`、`sweep_expired`；`member_import_routes.sweep_member_imports`（每小时由账户清扫器调用，连同上传文件与无主旧文件）；Q4（2026-09-28）前没有调用方 |
| 安全活动记录（含 IP）、邮件投递记录、通知：长期保留，无自动清理 | P | `grep -rn "DELETE FROM audit_events"` 无结果；`store.py:417-424` `mail_outbox`；`notifications.py` 无清理 |
| 删除的消息对他人显示「消息已删除」，正文清空、序号保留 | P | `docs/platform/messaging.md:14, 28` |
| 删除的文档项目标记为已删除；底层文件不自动回收 | P | 研究清单「Docs artifacts/blobs (soft delete only, blobs never garbage-collected)」；`packages/core/synwevo_core/journal.py:108-110` |
| **未写进页面**：「未使用的附件 1 小时后删除」 | — | `messaging.py:754` `sweep_unused_attachments` 没有调度器调用（研究清单「Attachment sweep scheduler … never executed」），所以不声称 |

### 2.7 导出与删除

| 陈述 | 页面 | 出处 |
|---|---|---|
| 导出在账户页「数据与账户」，需当前口令；JSON（`synwevo-account-2`）含资料、工作空间与组织成员关系、通行密钥名称、安全活动、头像，以及本人发出的消息（正文、时间、所在会话、附件文件名）、所在会话、消息设置、屏蔽列表、通知偏好；不含他人消息、附件文件与文档内容（CI-81） | P | `synwevo_core/messaging.py` `messaging_export`、`blocks_for_export`； `services/identity/synwevo_identity/account_routes.py:229-247`；`profile.py:34-38` `public_profile`；`services/identity/web/src/pages/account/PrivacySection.tsx:27`（`#privacy`） |
| 删除：需当前口令；立即退出全部登录、撤销待接受邀请、进入 7 天缓冲；邮件链接可撤回 | P S | `account_routes.py:249-275`（`invalidate_access`、撤销 invitations、`account_recoveries`） |
| 到期后删除口令、通行密钥、两步验证、恢复码；清空资料与邮箱；账户标识与邮箱占位保留；组织内贡献保留 | P S | `profile.py:176-197` `finalize_deletions`（`email_key remains reserved`；`preserve immutable user IDs and organization contributions`） |
| 到期后退出全部会话：本人是群主的群交给最早加入的管理员（无则最早加入的成员），只剩本人的群归档；私聊安静关闭、对方保留记录（CI-81；离开组织同理） | P | `synwevo_core/messaging.py` `Messaging.depart`；`profile.py` `depart_deleted_accounts`（到期清扫与既往已删除账户） |
| 前提：唯一组织所有者或最后一位平台管理员须先移交 | P S | `profile.py:142-156` `check_deletion_constraints` |
| 组织所有者可冻结 / 删除 / 7 天内恢复组织 | S | `organizations.py:484-500` `transition`（`require(owner=True)`） |
| 平台管理员可停用账户，停用即刻结束全部登录 | S | `services/identity/synwevo_identity/admin_routes.py:424-445` |

| 导出另含 Agent：对话的可见文字、执行的操作（名称、空间、状态）、记忆、设置（只说明是否设置了个人密钥）、数据同意及其历史、运行授权（`authorization`：automatic / confirmed / consent；含旧报价与持续付款授权）、接受的条款版本、举报、个人钱包流水；不含模型的思考过程 / 私有回放、工具输入与结果、任何密钥 | P | `synwevo_core/agent_records.py` `export`；`account_routes.py` `export`（`result["agent"]`）；`services/identity/tests/test_agent_records.py` |
| 删除到期后清除 Agent 对话、消息、轮次、事件、工具调用、记忆、设置（含密封的个人密钥），以及举报里的备注与摘录；运行授权（含旧报价、付款授权）、账本、同意、条款接受记录与内容安全记录以永久、已无姓名的账户标识保留；备份恢复后由 `user_deleted` 重放再次清除 | P | `agent_records.erase`；`profile.py` `finalize_deletions`；`journal.py` `user_deleted` |
| 删除时钱包未用余额作废，不退还（充值不可退款）；删除前在账户页明示 | P T | 所有者决定 2026-10-05（不退款）；`services/identity/web/src/pages/account/PrivacySection.tsx` |

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

### 2.9 AI 与模型提供方（数据与隐私 `privacy-ai`）

| 陈述 | 页面 | 出处 |
|---|---|---|
| 第一次发消息前单独同意（带版本，记录；在「开始使用 Synwevo Agent」步骤中单独勾选，不预勾选、不与条款捆绑）；版本更新再问；撤回后不再发起任何模型调用（含进行中回答的下一次调用） | P | `agent_safety.py` `PROVIDER_TERMS["deepseek"].version`、`ProviderConsents.accept/withdraw/require`；`engine._check_context`；撤回写入安全日志 `agent_provider_consent_withdrawn`；`apps/suite/tests/test_agent_safety.py` |
| 发送：本对话消息、同一对话较早消息、记住的偏好、AI 处理政策允许的工作空间内容；组织空间默认不允许，个人空间由本人决定 | P | `engine._api_history`、`_system`；`tools.py` `policy_allows` / `workspace_policy` |
| 受托处理方：杭州深度求索人工智能基础技术研究有限公司（DeepSeek）；以开放平台用户身份调用，无合作或背书 | P | `agent_safety.py` `PROVIDER_TERMS`（名称须与 DeepSeek 隐私政策逐字复核，见 §5） |
| 跨境：服务器在东京；内容传给 DeepSeek，按其隐私政策存储在中国境内 | P | 所有者决定 2026-10-05；DeepSeek 隐私政策（cdn.deepseek.com，2026-10-05 读取） |
| 可能用于改进模型；正在取得书面确认 | P | 所有者决定 2026-10-05：书面确认前如实写 DeepSeek 隐私政策允许的范围（§5 待办） |
| 个人密钥：以本人账户发给该密钥的服务商 | P | `provider.py` `resolve_credential`（`personal`） |
| 内容安全：管理员规则（带版本、审计）+ 提供方过滤；被拦只记规则、方向、长度；举报只在本人选择时附原文 | P A | `agent_safety.py` `SafetyRules` / `SafetyEvents` / `AgentReports`；`agent/safety.py`；`admin_agent_safety.py` |
| 每条回答与导出注明「由 AI 生成，可能有误，仅供参考」 | P A | 对话界面（chat-ux 包，`aiLabel.ts`、`exportChat.ts`）；账户导出 `agent_records.export` 的 `notice` |
| Agent 对话与记忆保留到本人删除或账户删除；内容安全记录长期保留（无内容）；计费记录删除后去名、按法定期限保留 | P | `agent_records.py`；`suite_schema/agent_safety.py` |

### 2.10 服务条款、AI 使用规范、定价

| 陈述 | 页面 | 出处 |
|---|---|---|
| 充值一经完成不可退款，未用余额不退（适用法律强制要求的除外）；没有退款流程 | T R | 所有者决定 2026-10-05「不退款」；org-payments 包没有退款路径（拒付另行处理） |
| 余额闭环：只用于 Synwevo 服务，不能转让、提现、兑换现金，不计利息 | T R | 所有者决定（Credits 闭环）；账本只有钱包 → 销售分录（`billing/ledger.py`） |
| 拒付 / 争议：冻结钱包、记账，余额不为负 | T | org-payments 包（`billing_account_freezes`、chargeback 分录；`billing_journal_apply` 触发器 `wallet_negative`） |
| 按实际用量扣费；发送即开始，不逐次确认；每条回答有最高金额（默认按当时价格的单次调用预留 × 12，可在 Agent 设置中调低），输入框旁显示，实际扣费不超过它；价格 / 模型变化只适用于之后开始的回答，聊天页提示价格已更新，进行中的回答在下一次模型调用前停止，已完成的调用按原价格结算，不追溯改价（所有者决定 2026-10-23，DECISIONS D17） | T R | `agent/quotes.py`（`issue_automatic`，准入事务内签发并绑定；`authorize` 在每次调用前核对路线与价格）、`engine.py`、`ops.py`（仅 `via=ui`）；`gateway.py` 结算可信用量；`agent.billing.status.price_notice` |
| 未完成调用（截断 / 过滤 / 中断 / 停止）是否计费按管理员公布的策略，默认平台承担 | T R | 共享契约 incomplete-call policy（gateway 包，默认全部 false） |
| 免费额度：本部署开放时，验证邮箱后一次 1 元，同一邮箱一次 | T R | 所有者决定 2026-10-05；org-payments 包 `billing/welcome.py`（验证地址、每账户一次、每规范化地址一次、每日上限） |
| 组织付费：所有者 / 获授权管理员充值组织钱包、设成员额度；成员选付款方；组织只看按成员的用量与金额 | T R | 所有者决定；org-payments 包 `agent/payers.py`、`org_payers.py`；usage-pages 包 group=member |
| 目前只有 Synwevo Agent 按用量收费，以人民币计价 | R | 账本只有 `agent` 命名空间（`configuration.py` `Ledger(..., namespace="agent")`） |
| 本页不列具体单价：单价按版本发布，Agent 输入框旁与 Agent 设置中显示当前单价、价格版本与每条回答的最高金额 | R | `billing/catalogue.py`（版本化售价）；`agent.billing.offer` |
| 不允许的内容 / 行为与执行方式（规则检查、提供方过滤、只记必要信息、举报、暂停） | U | `agent_safety.py`；`agent/safety.py`；`admin_agent_safety.py`（暂停 `platform.users.manage`，写入安全日志 `agent_access_suspended`） |
| 不转售 / 不提供可直接调用模型的接口 | U | 需求 §1（本期不对外开放 OpenAI 兼容端点） |
| 开始使用 Synwevo Agent 时一次性接受条款、AI 使用规范与定价（单独勾选，不预勾选），记录接受的版本；文件版本更新后再请一次（单价变化不改版本） | T | `synwevo_core/terms.py` `DOCUMENTS`（terms / pricing 版本 2026-10-24，ai-use 2026-10-05）、`TermsAcceptances.missing/accept`（`agent.terms.accept`，context `agent_setup:<op run>`）；自动授权路径要求事先接受（`terms_acceptance_required`）；旧 `quote_id`/`consent_id` 路径仍由 `record_paid_use` 记录 |

「页面」列另加：T = 服务条款，U = AI 使用规范，R = 定价，A = 产品内（Agent / 账户）。

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
| `agent.released` / `agent.provider` / `agent.model_configured` | 门户进程自己的库与配置（`public.py` `agent_fact`）：注册表 `apps` 行 `agent` 是否 `active`；`SYNWEVO_AGENT_PROVIDER`；DeepSeek = `ProviderSettings(SYNWEVO_BILLING_ENVIRONMENT).read()` 已启用，旧提供方 = 只读平台密钥是否存在 | 未开放：不会向 AI 模型发送任何内容 / 已上线，模型服务尚未开放 / 已开放：经你单独同意后，发送给模型提供方 DeepSeek / 暂时无法读取 |

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
4. **正式条款**：2026-10-05 起有带版本与生效日期的服务条款、AI 使用规范与定价页；2026-10-24 版（发送即运行，不逐次确认）
   起在「开始使用 Synwevo Agent」步骤中一次性勾选接受并记录版本；数据与隐私、服务说明两页仍写「不是正式法律条款」。
   注册时是否另行勾选同意、2026-10-24 版条款文本的法律审阅（尚未经法律顾问复核，DECISIONS D17 的法律依据只是简述，不是法律意见），仍待运营方决定。
5. **年龄与资格限制**：目前注册不做年龄或身份检查。
6. **费用、退款、可用性承诺（SLA）**：收费与不退款已由所有者决定（2026-10-05，见 §2.10）；可用性指标仍没有，页面不写。
11. **DeepSeek 书面确认**（所有者负责）：API 数据是否用于训练、保留期限；取得前隐私页写「依 DeepSeek 隐私政策，可能用于改进模型，境内存储」。
    受托处理方的法定名称在发布前须与 DeepSeek 隐私政策逐字复核。
12. **中国大陆网信登记**（所有者法务事项，记录为待办）：面向全球市场；页面不写备案号。
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

## 首页常见问题（CI-91）

八问：用途、个人或团队、成员加入、谁能看到、私信邮件、文件格式、导出与删除、数据位置与 AI。每条答案只复述本文件事实表与隐私页已写、代码可证的内容；新增问题同样先进事实表。费用问题在持有人决定收费方式前不写。
