# 风险触发的人机验证（Cloudflare Turnstile，服务端校验）

> 状态：2026-09-25 实现（CI-21）。本文写的是**当前代码的行为**；"未做"的部分在 §8。

## 0. 出发点：现状有什么、没有什么（实现前）

| 入口 | 已有控制 | 缺口 |
|---|---|---|
| `POST /api/auth/register`、`/api/auth/email/resend` | 10 次 / 来源地址 / 15 分钟；3 次 / 邮箱 / 小时 | 换地址、换邮箱即可绕过窗口；每次成功都是一行账户加一封邮件 |
| `POST /api/auth/forgot` | 10 / 地址 / 15 分钟；3 / 账户 / 小时；同一答案不泄露账户存在 | 同上，且被攻击者可用它不断作废持有人的重置链接 |
| `POST /api/auth/login/email/start` | 10 / 地址 / 15 分钟；3 / 邮箱 / 小时 | 同上：验证码轰炸 |
| `POST /api/auth/login` | 账户退避；地址 30 次失败 / 15 分钟后封禁 | 撞库者用地址封禁把持有人锁在门外；没有"证明是人再继续"的出口 |
| 邀请、Passkey、已登录的重发 | 会话或凭据绑定，已有预算 | 不是本轮对象 |

没有：任何人机验证；`X-Forwarded-For` 已只信任受控代理（`client_ip`），计数器已在共享数据库中。

## 1. 决策记录

| 项目 | 结论 |
|---|---|
| 问题与基线 | 四个匿名入口每次成功都发一封邮件或建一行账户，窗口限额只能约束单个地址/邮箱，多来源自动化仍然便宜；口令登录的地址封禁是"锁住持有人"的工具。 |
| 成熟参考 | Cloudflare Turnstile 官方文档（developers.cloudflare.com/turnstile，读取 2026-09-25）：`siteverify` 契约（`secret`/`response` ≤ 2048 字符/`remoteip`/`idempotency_key`；返回 `success`、`challenge_ts`、`hostname`、`action`、`error-codes`；令牌 300 秒有效、只能校验一次，重放得到 `timeout-or-duplicate`）；客户端渲染（`api.js` 必须从官方 URL 加载，不得代理或缓存；显式渲染 `turnstile.render` 的 `action`/`language`/`size`/回调）；CSP（`script-src` 与 `frame-src` 允许 `https://challenges.cloudflare.com`）；测试密钥（`1x…AA` 恒通过、`2x…AB` 恒失败、`3x…FF` 强制交互；测试密钥可在任意主机名含 localhost 使用，生产密钥拒绝假令牌）。OWASP ASVS/认证 Cheat Sheet 对"限速与自动化防护应在服务端执行"的要求。 |
| 对比与选择 | (a) Turnstile：项目已在 Cloudflare 之后，免费额度足够，无需图形题，键盘可用，隐私优于 reCAPTCHA；(b) 自建 PoW/验证码：无数据集与维护能力，且图形题违反"不默认纯视觉"要求；(c) 只加严限额：不能区分人与脚本，只会把持有人锁得更死。选 (a)，**服务端校验**为唯一判定；不引入第三方 SDK，`siteverify` 用已有的 httpx 直连（约 200 行 `synwevo_core/challenge.py`）。 |
| 预期变化 | 配置了密钥的部署：注册、重发验证邮件、找回口令、邮箱验证码登录的表单显示一个 Turnstile 组件，请求体多一个 `challenge_token`；口令登录在同一地址连续 3 次失败后才要求它。未配置：一切照旧，`/api/auth/state.challenge = null`，页面不加载任何外部脚本。 |
| 边界与风险 | 组件脚本被拦截（隐私插件、企业代理）时表单无法提交——这是明确的拒绝而非放行，页面给出原因与重试；Cloudflare 不可达时 503 `challenge_unavailable`，注册/找回在故障期间不可用（有意，见 §5）；令牌只对同一 `action` 与允许的主机名有效；秘密只在服务端；测试密钥只在隔离实例。 |
| 验收 | §7：缺失/伪造/已用/过期/跨 action/跨 hostname 令牌都不能放行且不产生账户或邮件；正常挑战后业务可完成；口令登录第一次不弹挑战、3 次失败后必须带令牌；验证服务离线时 503 且无副作用；未配置时行为不变；三语言、手机、键盘可完成；真实组件 + Cloudflare 测试密钥在隔离实例上走通。 |
| 发布与恢复 | 无数据迁移。环境变量控制启停：生产先不设密钥部署代码（行为不变），账户持有人在 Cloudflare 仪表盘创建 Widget（主机名 `accounts.wegreen.ltd`，模式 Managed）后把两把密钥写进 VPS 的 `runtime.env`，重启 accounts 即生效；回滚只需删掉两把密钥。 |

## 2. 策略：谁被挑战、何时

| `action` | 入口 | 策略 |
|---|---|---|
| `register` | `POST /api/auth/register` | 每次（匿名建账户 + 邮件） |
| `resend` | `POST /api/auth/email/resend` | 每次（匿名重发验证邮件） |
| `forgot` | `POST /api/auth/forgot` | 每次（匿名重置邮件） |
| `login_email` | `POST /api/auth/login/email/start` | 每次（匿名验证码邮件） |
| `login` | `POST /api/auth/login` | **风险触发**：同一来源地址在 15 分钟窗口内已失败 ≥ 3 次（`Identity.address_failures`），之后每次都要在校验口令**之前**带令牌 |

不挑战：已登录会话的重发（`/verify-email/send`，会话绑定并限次）、Passkey 登录、邀请（已认证，50/24h 预算）、
所有已登录业务与路由切换、机器 API、Webhook、探针。挑战状态与登录状态分开：不改 `SessionProvider`，不触发重载。

## 3. 服务端校验：`packages/core/synwevo_core/challenge.py`

* `Turnstile.verify(token, action=…, remoteip=…)`：空/过长/含控制字符的令牌本地拒绝，不发网络请求；否则 `POST siteverify`
  带 `secret`、`response`、`remoteip`、随机 `idempotency_key`；网络错误、5xx、`internal-error` 用**同一个** `idempotency_key`
  重试一次（重试的令牌不会被 Cloudflare 判成"已用"），再失败抛 `ChallengeUnavailable`。
* 判定顺序：`success` → `hostname ∈ SYNWEVO_TURNSTILE_HOSTNAMES`（默认 `SYNWEVO_ISSUER` 的主机）→ `action == 期望` →
  `challenge_ts` 不早于 300 秒。任何一项不满足都是拒绝，理由分别为 `invalid`/`spent`/`hostname`/`action`/`expired`。
* `invalid-input-secret`（配置错）按**不可用**处理而不是"验证失败"：运维问题不能变成让用户反复重试，也不能变成放行。
* 一次性：由 Cloudflare 强制（`timeout-or-duplicate`）；并发提交同一令牌只有一个能成功。事务绑定：`action` 把令牌钉在
  一个表单上；令牌不写库、不写日志、不进审计（审计只有 `action`、`reason`、`hostname`、前 4 个 error-code）。
* 路由助手 `require(request, body, action, audit=…)`：未配置 → 直接返回；无令牌 → 400 `challenge_required`；拒绝 →
  400 `challenge_failed{reason}`；不可用 → 503 `challenge_unavailable{retry_after: 30}`（`Retry-After` 头同步）。
* 位置：在字段格式校验之后、限额窗口与 Argon2 之前——畸形请求不花 siteverify，无令牌的脚本不消耗任何计数器。

## 4. 页面：`services/identity/web/src/pages/org/Challenge.tsx`

`useChallenge(action, {armed, enabled})` 读取 `/api/auth/state.challenge`；需要时才注入 Cloudflare 的 `api.js?render=explicit`
（官方要求的精确 URL，不代理不缓存），显式渲染：`theme: light`、`size: flexible`、`language` 跟随界面语言
（zh-cn / zh-tw / en）、`refresh-expired: auto`。组件有四个可见状态（加载中 / 已过期正在刷新 / 出错 + 重试 / 已通过），
`role=status|alert`，键盘可达；令牌放进请求体 `challenge_token`。服务端回 `challenge_required`/`challenge_failed`
时组件重置；登录表单收到 `challenge_required` 时**保留已输入的口令**并显示组件，用户完成后再次提交即可。
CSP：配置了密钥时 `script-src`/`frame-src` 各多一个 `https://challenges.cloudflare.com`（`http.py:_with_challenge_origins`），
未配置时策略不变。

## 5. 故障策略

| 情况 | 行为 |
|---|---|
| Cloudflare siteverify 不可达 / 5xx / internal-error（重试一次后） | 503 `challenge_unavailable`，`Retry-After: 30`；**不放行**。注册、找回、验证码登录在故障期间不可用；已登录业务、口令登录（未触发风险）与机器接口不受影响 |
| 组件脚本被浏览器拦截 | 页面显示原因与"重试"；提交会被服务端 400 `challenge_required` 拒绝。替代路径：换网络/关闭拦截；口令登录未触发风险时仍可直接登录 |
| 秘密配置错误 | 与不可达同样处理，审计 `auth.challenge result=error` 记录原因，运维在日志中可见 |
| 令牌过期（>300 s） | 组件自动刷新；若仍提交旧令牌，服务端 400 `challenge_failed{reason: spent|expired}` 并重置组件 |

## 6. 配置与上线（运维 / 账户持有人）

1. Cloudflare 仪表盘 → Turnstile → Add widget：主机名 `accounts.wegreen.ltd`，Widget mode **Managed**（默认；必要时才交互），
   预清除（pre-clearance）不开。得到 Site Key（公开）与 Secret Key（保密）。
2. VPS：在 `/opt/synwevo/config/runtime.env` 追加 `SYNWEVO_TURNSTILE_SITE_KEY=…`、`SYNWEVO_TURNSTILE_SECRET=…`
   （`SYNWEVO_TURNSTILE_HOSTNAMES` 可省略，默认 `SYNWEVO_ISSUER` 的主机），`docker compose restart accounts`。
3. 核对：`GET https://accounts.wegreen.ltd/api/auth/state` 的 `challenge.site_key` 是刚创建的键、没有 secret；注册页出现组件；
   用一个新邮箱注册成功；审计里出现 `auth.challenge result=ok action=register`。
4. 回滚：删掉两把密钥并重启；行为回到本文 §0。

不要把密钥贴进聊天、提交或截图；不要在生产使用测试密钥（生产密钥拒绝假令牌，测试密钥对任何令牌都通过）。

## 7. 验收证据

* 单元/接口测试 `services/identity/tests/test_challenge.py`（9 项，siteverify 替身记录每次表单）：秘密与幂等键只发给 siteverify；
  跨 action、跨 hostname、已用、无效、过期、缺失、畸形分别拒绝且缺失/畸形不上网；重试复用幂等键；不可达/坏秘密/5xx 为不可用；
  `/api/auth/state` 有 site key 无 secret；注册无令牌 400 且无账户无邮件、坏令牌 400、跨 action 400、好令牌 202 且一封邮件；
  不可达 503 + `Retry-After` 且无副作用；找回/验证码/重发同样被挡；口令登录首次不挑战、3 次失败后必须带令牌；未配置时行为不变。
* 浏览器（隔离实例 + Cloudflare 公布的测试密钥，Chromium 与 WebKit，手机视口，三种语言）：组件在强制 CSP 下从
  `https://challenges.cloudflare.com` 加载脚本与挑战帧、产生令牌，服务端把令牌送到真实 siteverify。**实测（2026-09-25）测试密钥的
  令牌返回 `success: true`、`hostname: example.com`、无 `action`**，所以服务端先以 `hostname` 拒绝，隔离实例允许 `example.com` 后以
  `action` 拒绝——绑定检查在真实验证器前成立；失败密钥（`2x…AA`）返回 `success: false` → `challenge_failed{invalid}`。
  两种结果页面都在当前语言给出说明并保留表单；找回与验证码表单显示组件、口令登录不显示；0 CSP 违规。
  **浏览器里的完整注册通路需要真实 Widget 与真实主机名**（生产密钥，账户持有人的步骤），本轮无法用测试密钥证明。
  记录见 `docs/quality/verification.md` §16.21。

## 8. 明确未做

* 邀请滥用、Passkey 匿名 begin、已登录重发仍只靠现有预算与会话绑定（风险低、成本低）。
* 多实例下 API 网关的 300 次/分钟按密钥限额仍是单进程内存计数（§7.3 的下一批）；本轮涉及的所有窗口计数都在共享数据库中。
* 没有管理端"挑战失败统计"页面：审计事件 `auth.challenge` 可查询，不另建装饰性仪表盘。
* 生产尚未配置密钥（需要账户持有人在 Cloudflare 创建 Widget）。
