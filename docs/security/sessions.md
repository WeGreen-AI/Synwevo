# 会话、单点登录、重认证与撤权

本文件是会话层的实现契约。权限判断在 [permissions.md](permissions.md)；这里只回答"谁还登录着、
凭什么、以及怎么让它停下来"。

所有时长都是代码里的真实默认值，括号里是覆盖它的环境变量。改这些值不需要改代码，但改完要重新
核对本文件。

---

## 1. 三层会话，分开管理

平台有三层会话。它们**不是同一个东西的三份副本**——任何一层都不能由另一层直接推出来。

| 层 | 在哪个进程 | Cookie | 怎么产生 | 怎么结束 |
|---|---|---|---|---|
| 中央认证会话 | 账户服务（`SYNWEVO_ROLE=accounts`） | `dw_session`，host-only，账户服务域名 | 口令 / Passkey 登录 | 平台登出、全设备撤销、账户停用、口令重置 |
| 应用会话（门户、Synwevo Docs 各一份） | 各应用自己 | `dw_session`，host-only，各自域名 | 走完授权码流程后本地签发 | 应用内退出、后通道登出、令牌撤销 |
| 令牌（access / refresh） | 只在服务端之间 | **不进浏览器** | 令牌端点 | 撤销、过期、刷新时重新核验 |

同名 Cookie、三个不同主机，因此是三个互不相通的值。这是本机 `verify_sso.py` 直接量过的性质之一。

**为什么不共享一个宽域 Cookie**：那会让"在账户服务登录过"自动等于"在每个应用登录过"，撤销其中
一个就必须撤销全部，而且任何一个应用被攻破就等于全部被攻破。走完协议是唯一把前者变成后者的途径。

### 1.1 Cookie 属性

`sessions.cookie_kwargs()`：`HttpOnly`、`Secure`（可配置）、`SameSite=Lax`、`Path=/`，**不设 `Domain`**。

- **不设 `Domain`** ⇒ host-only ⇒ 按 RFC 6265 不会发往兄弟主机。这是隔离的实现方式。
- **`SameSite=Lax` 而不是 `Strict`**：`Strict` 会让任何外部链接进入应用后的第一个请求丢掉 Cookie
  ——包括验证邮件和邀请邮件里的链接，于是人点了"确认"却落在登录页。`Lax` 仍然拒绝跨站 POST，
  剩下的由 CSRF 双提交覆盖。
- **`Secure` 由配置传入，不猜**：猜错两个方向都坏——强制开启会让 Cookie 在本机 HTTP 上静默不写入，
  在生产强制关闭则让它明文传输。本机用 `SYNWEVO_COOKIE_SECURE=0`，这也是那些主机名绝不能用于
  任何真实用途的原因。

> **不同端口不隔离 Cookie。** Cookie 协议按主机划分作用域并完全忽略端口。因此本机用
> `accounts.localhost` / `portal.localhost` / `docweave.localhost` 三个主机名，
> `scripts/verify_sso.py` 用 Python 的 `http.cookiejar`（真实规则）把隔离量出来，而不是假设。

### 1.2 会话行里存什么

`sessions` 表的主键是 **Cookie 值的 SHA-256**，Cookie 原文从不落地。拿到数据库副本的人无法把
任何一行重放成一次登录。

---

## 2. 两个时钟

每个会话有两个独立的过期时间，**在读取时强制执行**，不靠后台清理任务：

| 时钟 | 默认 | 环境变量 | 含义 |
|---|---|---|---|
| 空闲过期 | 12 小时 | `SYNWEVO_SESSION_IDLE_S` | 最后一次请求之后多久作废 |
| 绝对过期 | 30 天 | `SYNWEVO_SESSION_ABSOLUTE_S` | 无论多活跃，从创建起多久必须重新登录 |
| 管理员空闲过期 | 1 小时 | `SYNWEVO_ADMIN_IDLE_S` | 平台管理员的空闲时钟更短 |
| 「保持登录」空闲过期 | 14 天 | `SYNWEVO_SESSION_PERSISTENT_IDLE_S` | 登录时勾选「在此设备上保持登录」的中央会话；绝对过期不变 |

只有绝对过期能防住"一直有人用着所以永远不过期"的会话。只有空闲过期能让离开的设备自己失效。
两个都要。

**在读取时执行**是关键：一个已过期的会话行即使还在表里，`Sessions.load()` 也不会返回它。所以
清理任务的延迟不会变成一个可用的过期会话。

### 2.1 保持登录（记住登录）

登录表单上的「在此设备上保持登录」是一个**明确的、默认不勾选**的选择，只影响两件事：

| 项 | 普通登录 | 勾选「保持登录」 |
|---|---|---|
| 空闲过期 | 12 小时 | 14 天（`SYNWEVO_SESSION_PERSISTENT_IDLE_S`） |
| 绝对过期 | 30 天 | 30 天，**不变** |
| Cookie | 浏览器会话 Cookie（无 `Max-Age`） | `Max-Age` = 距绝对过期的剩余秒数，`dw_session` 与 `dw_csrf` 同步 |
| 平台管理员 | 1 小时空闲 | **忽略此选项**：仍是 1 小时空闲、会话 Cookie |
| 记录 | `sessions.persistent = 0` | `sessions.persistent = 1`，设备列表标记「保持登录」 |

它**不是**：

- 保存口令，或把长期令牌写进 `localStorage`——Cookie 里仍然只有不透明会话值，服务端行才是会话；
- 降低任何敏感操作的要求——重认证（§5）、第二因素和 `strict` 检查与普通会话完全一样；
- 「信任此设备、减少 MFA」——本轮没有这种能力，勾选框也不会悄悄变成它；
- 应用会话的持久化——门户与 Synwevo Docs 的应用会话仍是浏览器会话 Cookie。重开浏览器后应用会
  经统一登录**静默**续接：账户服务的中央会话还在，`/oidc/authorize` 不再要求输入口令就签发授权码。

只接受 JSON `true`；字符串 `"true"`、`1`、`"on"` 都视为未勾选（`remember_choice()`）。
服务端行过期后，浏览器里仍在的 Cookie 被 `load()` 拒绝，`/api/auth/me` 返回 401。
验证：`services/identity/tests/test_remember_me.py`。

---

## 3. CSRF

双提交：`dw_csrf` Cookie（**非 HttpOnly**，页面要能读到并回显）+ `X-Synwevo-CSRF` 请求头。

- 只对 `/api/` 下的非安全方法检查。
- `/api/auth/login` 等匿名端点豁免——它们还没有会话，而且没有正确凭据本身就是无用的。
- `/oidc/token`、`/oidc/revoke`、`/auth/backchannel-logout` **完全不看 Cookie**，因此不需要也不
  应该有 CSRF 检查：它们的凭据在请求体里。一个接受会话 Cookie 的令牌端点就是一个 CSRF 形状的
  发币机。

CSRF Cookie 可被脚本读取不是漏洞：它本身不是凭据，只证明请求来自我们自己的页面。

---

## 4. 单点登录

### 4.1 协议

授权码流程 + PKCE S256，由 Authlib 1.6.5（BSD-3-Clause）提供协议实现。

| 项 | 值 | 说明 |
|---|---|---|
| 授权码有效期 | 60 秒 | 短：要么立刻换掉，要么就不该换 |
| 授权码使用次数 | 1 | 条件 UPDATE 保证；**重放会撤销该授权签发的全部令牌** |
| access token | 15 分钟（`SYNWEVO_ACCESS_TTL_S`） | 不透明随机值，库里只存 SHA-256 |
| refresh token | 14 天（`SYNWEVO_REFRESH_TTL_S`） | 仅在申请了 `offline_access` 时签发 |
| id_token | 5 分钟 | RS256，只用于客户端验证身份一次 |
| JWKS 客户端缓存 | 300 秒 | 遇到未知 kid 会强制刷新一次再判定签名错误 |

`state`、`nonce` 和 PKCE verifier 都存在依赖方的服务端（`rp_auth_requests`），浏览器只拿一个
不透明 id（`dw_authreq`，10 分钟）。所以这三个值都不可能由"把人引到这个链接的人"选定。

### 4.2 returnTo

登录后的落地地址由服务端约束，见 `rp.safe_next()`：只接受本站路径。以下全部落到 `/`：

- `//evil.example.com/x`（协议相对 URL，浏览器当作绝对地址）
- `/\evil.example.com`（部分浏览器把反斜杠规范化成斜杠）
- 任何含 `://` 的值

授权端点的返回目标是**服务端用自己的 URL 重建的**，不是客户端参数。

### 4.3 认证时间与认证方式

`auth_time` 和 `amr` 随授权码传递，写进应用会话的 `reauth_at` / `amr`。

`amr` 记录**这次实际发生了什么**：

| 值 | 含义 |
|---|---|
| `password` | 口令 |
| `password+totp` | 口令 + TOTP |
| `password+recovery` | 口令 + 恢复码 |
| `passkey` | Passkey，但认证器**只证明了持有** |
| `passkey_uv` | Passkey，且认证器在这次仪式里**验证了本人**（PIN / 指纹） |
| `email` | 发到账户邮箱的一次性验证码（证明控制邮箱，**一个因素**） |
| `email+totp` / `email+recovery` | 邮箱验证码 + 第二因素 |
| `idp:<provider>` | 外部 OpenID 提供商（如 `idp:google`）登录 |
| `idp:<provider>+totp` / `+recovery` | 外部提供商 + 第二因素 |

第二步 `/api/auth/login/mfa` 从 MFA 票据的 `first_factor` 得知第一步是什么（`password`、`email`
或 `idp:<id>`），所以 `amr` 记录的永远是**实际发生的组合**，而不是"口令+TOTP"这一种写法。

**「账户有 Passkey」不是这里面任何一个值。** 需要抗钓鱼且已验证本人的策略查 `passkey_uv`；
用"账户拥有什么"代替"这次用了什么"是这套区分存在的原因。

SSO 创建本地会话时，`reauth_at` 取的是**中央认证发生的时间**，不是"现在"。否则一次只是复用了
两小时前中央会话的登录，会被记成刚刚重认证。

---

### 4.4 邮箱验证码登录

`POST /api/auth/login/email/start {email}` → 一律 202、同一响应形状（不说明地址有没有账户）；
`POST /api/auth/login/email/verify {email, code, remember}`。

| 项 | 值 |
|---|---|
| 形式 | 6 位数字**验证码**，不是链接——邮件扫描器点开链接不会替人登录 |
| 有效期 / 使用次数 | 10 分钟 / 一次；新请求让旧码失效 |
| 错误尝试 | 每个码 5 次后作废；账户级退避桶与 TOTP 相同 |
| 限流 | 每来源地址 15 分钟 10 次；每地址（含不存在的地址，按哈希计）每小时 3 次且间隔 ≥ 60 秒 |
| 存储 | 只存 `sha256(row_id:code)`；发出后 `mail_outbox` 中的正文把验证码替换为占位（`redact()`） |
| 资格 | 账户 `active` 且邮箱已验证。平台管理员同样可以收码登录，但得到的是一因素会话（§4.6）——按钮和响应形状不因账户是否为管理员而不同，验证前不泄露任何账户属性 |
| 第二因素 | 启用 TOTP 的账户在验证码之后仍要完成第二步；不是绕过 MFA 的备用路径 |

验证：`services/identity/tests/test_email_code_login.py`。

### 4.5 外部 OpenID 提供商（Google / Microsoft）

账户服务对外部提供商是**依赖方**，实现与 §4.1 的平台内依赖方同构：授权码 + PKCE S256，`state`、
`nonce`、verifier 存在 `external_auth_requests` 行里，浏览器只带一个 host-only、10 分钟的
`dw_extreq` 请求 id；回调时单次消费该行，然后服务端换码（`client_secret` 与 `code_verifier` 在
请求体里、`trust_env=False`），校验 id_token 的签名（JWKS）、`iss`（Microsoft 多租户按 `tid`
代入模板）、`aud`/`azp`、`nonce`、`exp`/`iat`、`sub`。

| 情形 | 结果 |
|---|---|
| `(provider, sub)` 已关联 | 直接登录（`amr=idp:<id>`）；启用 TOTP 的账户走第二步 |
| 未关联，提供商**已验证**的邮箱对应现有账户 | **不合并**。写入 10 分钟的关联要约，落到 `/login#offer=…`；输入该账户口令（启用 TOTP 时再加验证码）后才关联并登录 |
| 未关联，邮箱已验证，无现有账户，注册开放 | 创建已激活、无口令的账户并关联；落到 `/onboarding` |
| 邮箱未验证 | 不注册、不匹配；提示用口令登录后在账户页关联（按 `sub` 关联，不看邮箱） |
| 注册未开放 | `registration_closed` |
| 平台管理员 | 与其他账户相同地登录（`amr=idp:<id>`），但会话在补上第二个因素前不能进入平台管理（§4.6） |

关联/解除关联在账户页进行，都要求 `scope=mfa` 且 `strict` 的近期重认证；解除关联不能移除账户
最后一种登录方式（口令、Passkey、其他外部身份至少剩一个）。两者都发安全通知邮件。
提供商只在配置了 `client_id`/`client_secret` 时出现在登录页（`/api/auth/state.external_providers`）；
配置不完整的提供商让账户服务在启动时拒绝，而不是显示一个不能用的按钮。
回调地址固定为 `<SYNWEVO_ISSUER>/auth/external/<id>/callback`。

验证：`services/identity/tests/test_external_identity.py`——用一个**测试用 OpenID 提供商替身**
（真实 RS256 签名、可注入篡改）覆盖正反用例；它证明 Synwevo 这一侧的检查，不证明与 Google /
Microsoft 的实际联调。

### 4.6 认证强度与管理会话（分阶段认证）

三个问题分开回答：提供商/邮箱在平台层是否可用；这个人是否完成了第一阶段身份验证；这次会话的**认证强度**
是否满足其角色的要求。前两者决定能否登录；只有第三个决定能否管理。

`synwevo_core.sessions.admin_capable(amr)` 是唯一的判定点：

| `amr` | 因素 | 管理能力 |
|---|---|---|
| `password`、`password+totp`、`password+recovery` | 口令（本平台策略把口令视为满足管理的基线，保持既有管理员登录方式） | 有 |
| `passkey_uv` | 认证器验证了本人（PIN / 生物特征） | 有 |
| `passkey` | 只证明持有 | 无 |
| `email`、`idp:<id>` | 一个因素 | **无** |
| `email+totp`、`idp:<id>+totp`、`email+password`、`idp:<id>+password` | 两个不同因素 | 有 |

一因素会话下，`/api/auth/me` 的 `permissions` 为空、`admin_locked=true`；`/api/admin/*` 与 `/api/admin/api-platform/*`
返回 `403 step_up_required {scope: admin}`（`admin_routes._admin`、`api_platform.actor`）。管理外壳仍然发给管理员
（角色是真的），页面用这个字段显示「当前登录只验证了一个因素」和口令表单，而不是伪装成不存在。普通账户
功能（设备、口令、Passkey、关联）不受影响。

补第二个因素的方式：`POST /api/auth/reauth {password, scope}` 成功时把 `password` **追加进 `amr`**
（`Sessions.add_factor`），因为口令是与验证码/提供商不同的因素；`/api/auth/mfa/reauth` 同样。TOTP 账户在登录
第二步就已得到 `+totp`。没有口令的管理员（只用外部登录建立的账户）要先通过邮箱重置链接设置口令，或以
`passkey_uv` 登录。这一升级只作用于当前会话行，不改变账户，不延长会话时钟；页面切换不会再次挑战。

不要在登录页按"这个邮箱是不是管理员"改变按钮：验证前的登录页对所有地址同形。也不要因为某个管理员没有
绑定 TOTP 就全局隐藏别人可用的登录方式——这是本轮从上一轮策略改掉的部分。

验证：`services/identity/tests/test_session_strength.py`（判定表）、`test_email_code_login.py` 与
`test_external_identity.py` 中的管理员用例（403 → 口令 → `amr` 升级 → 200）。

---

### 4.7 人机验证（风险触发，服务端校验）

配置了 `SYNWEVO_TURNSTILE_SITE_KEY`/`SECRET` 的部署：`POST /api/auth/register`、`/api/auth/email/resend`、`/api/auth/forgot`、
`/api/auth/login/email/start` 每次都要在请求体带 `challenge_token`（Turnstile 组件产生），`/api/auth/login` 只在同一来源地址
15 分钟内失败 ≥ 3 次后才要求。服务端用 Cloudflare siteverify 核验并检查 `hostname`、`action`、有效期与一次性；缺失 400
`challenge_required`，不通过 400 `challenge_failed{reason}`，验证服务不可达 503 `challenge_unavailable`（不放行）。
`/api/auth/state.challenge` 给出站点密钥与被挑战的动作，未配置时为 `null`，页面不加载任何外部脚本。挑战状态与会话状态
无关：不改 `SessionProvider`，不重载，不影响已登录的导航。细节与验收见 `human-verification.md`。

## 5. 重认证（reauth）

危险操作要求近期、**带作用域**的重认证。

| 项 | 值 |
|---|---|
| 有效期 | 10 分钟（`SYNWEVO_REAUTH_S`） |
| 作用域 | 封闭集合：`mfa`、`admin` |
| 端点 | `POST /api/auth/reauth`，`{password, scope}` |

**作用域是封闭集合**，因为空作用域满足所有检查——它必须是请求无法要求的值。

### 5.1 两种严格程度

```
reauth_fresh(session, scope)                # 非严格：新登录（空作用域）也满足
reauth_fresh(session, scope, strict=True)   # 严格：必须为这件事重新输入过口令
```

`strict=True` 用在提示本身就是目的的操作上：

- 历史数据认领、停用账户、撤销他人会话、修改平台角色（`admin` 作用域）
- 登记或删除 Passkey（`mfa` 作用域）

没有 `strict`，刚登录一分钟的人永远看不到确认提示——**一个可以被静默跳过的确认等于没有确认**。

> 本轮之前只有一个硬编码的 `"mfa"` 作用域，于是"为添加 Passkey 证明身份"同时解锁了管理操作，
> 正是 `reauth_fresh` 自己的文档说要防的那种泄漏。

---

## 6. 退出与撤权

### 6.1 三个入口，三种影响范围

文案必须说清影响范围。退出当前应用**不是**退出平台。

| 操作 | 影响 | 入口 |
|---|---|---|
| 退出当前应用 | 只结束这个应用的会话 | `POST /api/auth/sign-out` |
| 退出平台 | 中央会话 + 全部已关联应用会话 + 它们签发的令牌 | `GET {issuer}/oidc/logout` |
| 撤销全部设备 | 该用户的所有会话 | 账户安全页 |

### 6.2 后通道登出

实现在 `platform/routes.py: _end_central_session()`。

**顺序是有意的：先在自己的数据库里撤销，再通知。**

通知让应用**及时**知道；数据库让答案**正确**。收不到通知的应用，下一次校验令牌照样失败。把
通知当成执行机制，"到处退出"就会变成"在碰巧应答了的应用上退出"。

- 登出令牌：RS256 签名，带 `sid`（精确到这一次登录）和 `jti`（去重），**不带 `nonce`**——
  一个能冒充 id_token 的登出令牌就是一次认证绕过，规范把"没有 nonce"定成校验规则正是为此。
- 依赖方校验：签名、`iss`、`aud`、新鲜度（±300 秒）、事件声明、无 `nonce`、`jti` 未见过。
- 重复通知 = 成功且无事可做。否则提供方会永远重试。
- 投递失败记录在 `client_sessions.logout_error`，不吞掉。
- 投递用的 HTTP 客户端设 `trust_env=False`——见 §8。

### 6.3 各类撤权的生效时限

| 事件 | 对浏览器会话 | 对访问令牌 | 对刷新令牌 |
|---|---|---|---|
| 应用内退出 | 立即 | 不变 | 不变 |
| 平台登出 | 立即（本机后通道同步完成） | 立即撤销 | 立即撤销 |
| 账户停用 | **下一次请求**立即拒绝 | 下一次调用立即拒绝 | 刷新时拒绝并撤销整条家族 |
| 口令重置 | 立即撤销全部 | 立即撤销全部 | 立即撤销全部 |
| 移出工作空间 | 下一次请求失去该空间 | 下一次调用拒绝 | 刷新时拒绝 |
| 撤销应用资格 | 下一次请求拒绝 | 下一次调用拒绝 | 刷新时拒绝并撤销家族 |
| 角色降级 | 下一次请求按新角色 | **收窄**而非撤销 | 同左 |

"下一次请求"不是缓存窗口：会话和令牌在每次读取时重新解析，账户状态、成员关系和应用资格都现查。
**本机没有任何跨进程缓存**，三个进程共用一个 SQLite 库，所以撤销没有分发延迟。将来若引入分发，
必须在本文件写明可测量的上限与失联策略；高风险写操作在无法判断撤销状态时**不得默认通过**。

### 6.4 刷新令牌轮换与重放检测

每个刷新令牌单次使用。用过的再次出现 ⇒ 它泄露了（正当持有者手里已经是下一个）⇒ **撤销整条
家族**（`oauth_tokens.family_id`），不只是被出示的那一个。

每次刷新都重新检查账户状态与应用资格。这才是短期访问令牌值钱的地方。

---

## 7. 异常状态

都必须可恢复，且**不产生无限重定向**：

| 状态 | 行为 |
|---|---|
| 未登录访问受保护页 | 依赖方 → `/auth/start?next=…` → 账户服务 `/login?next=…` → 回到原页面 |
| 身份服务不可达 | `503 idp_unreachable`，**不放行**。"连不上身份服务"永远不解析成"让他进来" |
| 会话过期 | 同"未登录" |
| 无使用资格 | 授权端点即拒绝（`access_denied`），依赖方落到 `/sign-in-failed`；资格在授权与回调之间被撤销则落到 `/no-access` |
| 应用停用 | 同上，理由写明 |
| 取消登录 | 停在登录页，不自动重试 |
| 未登记的登出跳转地址 | 400 `invalid_redirect_uri`，**不跳转也不回显**；提供方先校验参数，再执行登出，因此该失败不会撤销会话或令牌、发送后通道通知或清除登录 Cookie |

---

## 8. 与身份服务通信的出站连接

依赖方的 HTTP 客户端和后通道投递都设 `trust_env=False`，代理必须由 `SYNWEVO_OIDC_PROXY` 显式指定。

httpx 默认遵循环境里的 `HTTP_PROXY`，而这些连接上跑的是**授权码**和**客户端密钥**。而且
`NO_PROXY=localhost,127.0.0.1` **不匹配** `accounts.localhost`，所以这件事极容易在无人察觉时发生
——本机验证时就发生了：所有浏览器跳转正常，只有服务端换码那一步被静默送进代理并丢弃，表现为
回调处的 `Server disconnected without sending a response`。

---

## 9. 签名密钥

- 持久化在 `oidc_keys`。**每次启动重新生成会让所有已签发令牌失效**——那看起来就是一次没有原因的
  集体登出。
- 轮换：`manage.py keys rotate` 让旧密钥转 `retiring`，**仍在 JWKS 中发布**，因此此前签发的令牌
  继续可验证。等最长令牌有效期过去后再 `keys retire --kid …`。
- 私钥 (`oidc_keys.private_jwk`) 与数据库同库。拿到数据库文件即可取得签名能力——备份必须按密钥
  对待，见 [identity-migration.md](identity-migration.md) §5。

---

## 10. 工作空间切换

切换是一次服务端操作（`POST /api/auth/workspace`），**每次都重新核验成员关系**：从浏览器来的
id 不可信，一个人已被移出的空间必须立刻不能用。

切换后前端做**整页重载**而不是局部更新。哪些项目、模板、任务存在取决于工作空间，已打开的视图
和缓存都属于旧的那个；就地改指针会留下一个"这个标签页悄悄变成另一个租户了"的页面。

切换器只出现在项目列表页，不在项目内部——在一个打开的项目旁边放切换器，正是本设计要防的竞态。

---

## 11. 本机验证

```bash
python scripts/platform.py up
python tests/integration/verify_sso.py --email you@example.test --password '...'
python scripts/platform.py down
```

`verify_sso.py` 用 `http.cookiejar`（按主机作用域、无视端口的真实规则）量三个来源的会话值、
host-only 属性、端口无关性，以及跨应用登出。**38/38 本机通过。**

相关测试：`apps/docs/tests/test_sso_chain.py`（26）、`test_oidc_flow.py`（28）、
`test_oidc_logout.py`（15）、`test_passkeys.py`（19）、`test_recovery.py`（20）。

---

## 12. 前端会话状态、重验证与「返回工作」

### 12.1 状态机

`SessionProvider`（`packages/auth-client/web/src/session.tsx`）只暴露三个字段，含义固定：

| 字段 | 值 | 含义 |
|---|---|---|
| `me` | `undefined` | **尚未确定**：首次恢复中。页面显示中性占位，不显示「登录」，也不渲染受保护内容 |
| `me` | `null` | 已确认未登录 / 会话失效 |
| `me` | 对象 | 已确认登录（含 `admin_locked`、`return_targets`、`workspace_id`） |
| `error` | `ApiError` | 临时网络/服务异常（非 401）：保留已知外壳并提示，不当成未登录 |

规则：**状态未知时不渲染未登录界面；已确认登录后的普通路由切换不把身份重置为未知或未登录。**
公开首页的顶栏在 `me === undefined` 时是占位块，之后才决定「登录」还是「进入工作空间」（`PublicHome`
的 `signedIn` 三值）。

### 12.2 请求去重与重验证

| 场景 | 行为 |
|---|---|
| 首次进入 | `/api/auth/state` 与 `/api/auth/me` **并行**发出（原来串行，白等一轮 RTT）；门户同时发 `/api/portal/home`，先到者先填 |
| 站内路由切换 | 不发认证请求；页面请求各自发，外壳不重建 |
| 回到标签页（`focus` / `visibilitychange` / bfcache `pageshow`） | 后台重验证，**间隔不小于 30 秒**（`REVALIDATE_MIN_MS`）；期间已知身份保持显示，结果只替换数据。门户不再把仪表盘退回加载态——那正是「侧栏点了没反应」的根因：正在被点击的元素被卸载了 |
| 会话过期（401，非上游） | 一次可恢复的登录流程；`ApiError.upstream` 区分应用自身 401 与 Docs 转发上游的 401 |
| 403 / 404 / 429 / 网络 / 5xx | 各自提示，不清空会话、不跳登录 |
| 注销 / 切换账号 / 切换空间 | 请求代次递增，旧响应丢弃；切空间整页导航 |

前端去重与缓存**不替代**服务端鉴权：每个受保护请求仍由 `synwevo_core.http` 与业务 `authz` 检查。

### 12.3 「返回工作空间 / 返回 Synwevo Docs」

账户页所有子页都有两条出口：返回来源工作（顶栏与手机抽屉）、返回账户概览（子页）。候选目标来自服务端
（`/api/auth/me.return_targets`：配置的门户 `PORTAL_URL/workspace`，以及 `Apps.apps_for` 列出的、该人当前可
打开的应用的 `/auth/start?workspace_id=…` 入口）。页面只**选择**其一：门户与 Docs 链接到账户页时附
`from=platform` / `from=docweave`（`/api/auth/state.app_id`），账户页把它当作 id 在列表里匹配，匹配不到就回门户；
这个 id 只在 `sessionStorage` 保留到标签页关闭。**不接受任何 URL 形式的 returnTo、不读 Referer**，因此账户源
没有开放重定向。目标空间已删除或失去权限时，`apps_for` 不会列出它，返回门户由门户决定去向；不落入历史 `default`
空间，也不自动创建空间。

验证：`tests/integration/test_return_targets.py`；浏览器回归 `services/identity/scripts/ui_e2e_shell.py`。
