# 系统邮件设计系统（CI-44）

9.15：全部真实系统邮件统一为白蓝品牌、可完成任务的产品体验；HTML 与纯文本同源，三语言，手机单列。

## 1. 决策记录

| 项目 | 结论 |
|---|---|
| 问题与基线 | 账户服务的 7 类邮件与通知邮件（单条/汇总）全是纯文本（`EmailMessage.set_content`），没有品牌、没有按钮、没有层级；验证码与链接混在正文里，三种语言各自手写，措辞不齐。 |
| 成熟参考 | Postmark「Transactional email best practices」（2026-09-26 读取）：一封一件事、主行动突出、纯文本替代、可信发件人；Campaign Monitor「Email accessibility」（同日）：语义结构、替代文字、对比度、布局表格 `role="presentation"`；Can I Email `prefers-color-scheme`（同日）：深色媒体查询支持不一致，不能依赖。 |
| 对比与选择 | (a) 引入邮件模板引擎/MJML：新依赖、编译步骤，与现有 f-string 模板不匹配；(b) 只加 HTML 版而不动文本：两份内容会漂移；(c) **本方案**：一个 `Mail` 数据类（标题、段落、验证码、主行动、事实卡、条目、后续说明）+ `render()` 同时产出文本与 HTML（同一字段，不会漂移）；HTML 用内联样式的单列表格，白卡、品牌蓝按钮，Logo 为账户服务上的稳定公共 PNG（`/brand/email-logo.png`，从确认原图裁出，HTTPS、无 Cookie、无指纹，可被邮箱图片代理抓取），不依赖深色媒体查询（白卡显式背景 + 透明深色 Logo，自动反色下仍可辨）。 |
| 预期变化 | 每封邮件：品牌头 → 标题 → 一两句说明 → （验证码 / 事实卡 / 条目）→ 一个主按钮 + 写出的地址 → 有效期与「若非本人」说明 → 页脚（为何收到、自动发送）。`mail_outbox` 多一列 `html`，投递为 `multipart/alternative`（先文本后 HTML，均 quoted-printable）；文件投递与 SMTP 同一份 MIME。 |
| 边界与风险 | 安全：动态文本一律 `html.escape`（姓名、空间名、项目名可含 `<`/`&`），链接只来自 `SYNWEVO_PUBLIC_URL`，令牌链接沿用 `#token=` 片段（不进访问日志），验证码只在文本里以标签行出现且不带链接；主题不含验证码与令牌；HTML 无脚本、无背景图、无外链跟踪；存档时 HTML 与文本同样脱敏（`redact`）。三语言由模板决定，用户名称与内容原文保留。旧队列中的未发邮件仍是纯文本（`html` 为空则单部分）。 |
| 验收 | `test_mail_catalogue.py`（7 类 × 3 语言：主题/文本/HTML 一致、链接绝对且互相包含、Logo 路径、转义、无占位符；无公共源时文字品牌回退；条目与事实卡两部分都渲染）、`test_mail_transport.py`（`.eml` 为 multipart，文本部分含链接、HTML 部分含同一链接）、既有验证码登录/地址/通知套件；`mail_preview.py` 生成 27 份预览；隔离实例真实注册邮件的 MIME 检查；预览在 720/390 px 无横向滚动。 |
| 发布与恢复 | schema v19：`mail_outbox.html`（增量，旧行为 NULL）；回滚到 v18 代码时列被忽略、邮件回到纯文本。 |

## 2. 邮件清单（真实发送路径）

| 类型 | 触发代码 | 收件人 | 主行动 | 有效期 | 偏好 |
|---|---|---|---|---|---|
| 邮箱验证 `verify` | `registration.py`（注册）、`auth_routes.py`（重发）、`admin_routes.py`（管理员代发） | 注册地址 | 验证这个邮箱（链接一次性，页面确认） | 24 h | 不可关闭 |
| 已有账户 `account_exists` | `registration.py` | 已注册地址 | 直接登录；找回口令链接 | — | 不可关闭 |
| 登录验证码 `login_code` | `email_login.py` | 账户地址 | 无链接；验证码文本 | 10 min | 不可关闭 |
| 找回口令 `reset` | `auth_routes.py`、`admin_routes.py` | 账户地址 | 设置新口令（一次性） | 2 h | 不可关闭 |
| 安全提醒 `security` | `account_routes.py`、`auth_routes.py`、`external.py` | 账户地址 | 查看账户与安全 | — | 安全通知不可关闭 |
| 新邮箱确认 `email_change` | `account_routes.py` | 新地址 | 确认这个邮箱 | 1 h | 不可关闭 |
| 账户注销申请 `account_deletion` | `account_routes.py` | 账户地址 | 取消注销 | 7 天 | 原先是路由文件里的两段纯文本，本轮并入目录；打开链接不自动恢复账户；不可关闭 |
| 工作空间邀请 `invite` | `members.py`、`member_import_routes.py` | 被邀请地址 | 接受邀请（页面确认） | 邀请 TTL | 邀请策略 |
| 通知（单条/汇总）`notification` | `notify.py`（收件箱派发：立即/汇总） | 账户地址 | 查看（对象链接） | — | 通知偏好（类别、渠道、汇总） |

Docs 与门户不自行发信：它们的事件走收件箱 → 通知邮件。没有账单、营销等其它邮件业务。

## 3. 未做 / 需外部条件

* 真实邮箱客户端渲染（Gmail、Outlook/机构 Microsoft 365、Apple Mail，桌面/手机、深色、图片屏蔽）：需要一个明确授权的测试收件箱；预览与 `.eml` 已备好。
* 生产 `SYNWEVO_PUBLIC_URL` 指向账户服务，`/brand/email-logo.png` 随本批部署后可被外部抓取（待线上复查）。
* ~~邮件 Logo 在流水线外~~ CI-92（2026-09-28）：`scripts/brand_assets.py` 从确认原图的完整版生成 `static/email-logo.png`（84 px 高，即绘制高度 28 px 的 3 倍），`--check` 覆盖它；此前手工文件是 4.34:1、邮件按 4.29:1 的 120×28 绘制，Logo 被横向压扁约 3%，现按文件自身比例绘制为 124×28（`mail_layout.EMAIL_LOGO_SIZE`，测试把两者绑定）。地址不变，历史邮件随之显示正确比例。
* 预览文字（CI-92）：每封邮件在正文开头放一段隐藏的预览行，取邮件自己的第一句（或 `Mail.preheader`），不取验证码、事实卡或汇总条目（汇总里可能引用私信），最长 110 字；没有段落的邮件（如私信汇总）不放预览块。
