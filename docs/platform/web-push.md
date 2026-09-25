# 浏览器推送（Web Push）（CI-32）

通知中心（CI-22）与邮件之外的第三条渠道：用户在自己的设备上开启后，浏览器在通知中心有新内容时收到系统通知，
标签页关闭也能收到。推送只是「有新内容」的提示，记录仍在通知中心；没有任何送达/已读的假象。

## 1. 决策记录

| 项目 | 结论 |
|---|---|
| 问题与基线 | 9.3 B.5–6 要求真实的浏览器/Web Push 订阅、测试、撤销与失败恢复。现状（CI-22）：`notification_deliveries.channel` 只有 `email`，没有订阅表、没有 Service Worker、没有 manifest；账户页偏好只有「通知中心 / 邮件」两列。 |
| 成熟参考 | 标准与官方资料（均 2026-09-26 读取）：MDN Push API（Service Worker + `PushManager.subscribe` + VAPID `applicationServerKey`；端点是能力 URL，须保密；`pushsubscriptionchange` 需重新订阅）；WebKit 官方博文《Web Push for Web Apps on iOS and iPadOS》（iOS/iPadOS 16.4+，**必须先「添加到主屏幕」且 manifest `display` 为 standalone/fullscreen**，权限请求须来自用户手势，标准 W3C API，无需 Apple 开发者账号，服务器需放行 `*.push.apple.com`）；协议 RFC 8030（推送）、8188/8291（aes128gcm 加密）、8292（VAPID，JWT ≤ 24 h）。库：pywebpush 2.5.0（MPL-2.0，PyPI 标记 Critical，单人维护）会把 `requests` 与 `aiohttp` 及其十余个传递依赖带进生产镜像；它的两块基石 py-vapid 1.9.4（MPL-2.0）与 http-ece 1.2.1（MIT）都只依赖已有的 `cryptography`。 |
| 对比与选择 | (a) 只用站内 + 邮件：不满足要求；(b) 引入 pywebpush：一个功能换十几个新包，否决；(c) **py-vapid + http-ece + 平台已有的 httpx**（本方案，`synwevo_core.push` 约 200 行：密钥、订阅、队列、发送与结果分类）。保留：通知目录、偏好模型（加一列 `push`）、派发器（加一趟推送）、读者语言渲染。 |
| 预期变化 | 账户页「通知」：每类多一列「浏览器推送」（安全始终开启）；新增「浏览器推送」小节——说明、状态（HTTPS 才可用 / 浏览器不支持 / iPhone 先加主屏幕 / 已拒绝权限 / 可开启）、「在此设备上开启推送」（用户手势 → 注册 `/sw.js` → 请求权限 → 订阅 → 上报）、设备列表（此设备标记、最近推送、失败次数、发送测试、移除）。通知产生时若读者有活跃设备且该类推送开启则入队；派发器每 30 s 一趟，逐设备发送。 |
| 边界与风险 | 权限：订阅只属于登录用户与创建它的会话，登出即撤销，服务端不再向已登出会话的设备推送；端点从不返回（只回哈希）；每用户 ≤ 10 台设备；订阅 20/小时、测试 5/10 分钟限流。内容：推送体是通知中心已渲染的标题/正文/链接（读者语言），≤ 3000 字节，不含秘密。失败：404/410 撤销设备；429/5xx 与网络错误按退避重试（沿用邮件的 `RETRY_DELAYS_S`）；连续 5 次失败撤销设备；免打扰时段推迟非紧急项。密钥：VAPID 私钥存库（同 OIDC 签名密钥的做法），首次使用时生成，不轮换（轮换会让全部订阅失效，需另行设计迁移）。iOS：只在主屏幕 web app 内可用，manifest 由账户服务提供并在账户页注入 `<link rel=manifest>`。无法在无头浏览器里真实订阅（没有推送服务）：浏览器证据止于 Service Worker 注册与诚实的失败提示；真实设备上的收到与点击须人工验证。 |
| 验收 | `services/identity/tests/test_push.py`（4 项，推送服务替身）：密钥稳定；订阅/同端点合并/列表标记此设备/坏订阅 400/移除后 404；测试推送带 `Authorization: vapid t=…,k=…`、`Content-Encoding: aes128gcm`、TTL、Urgency，密文不含明文；一条通知推到全部活跃设备各一次，410 的设备被撤销，第二趟不重复；类别关推送不入队、安全类不能关；免打扰推迟普通项、紧急项照发；推送服务不可达 → 队列回退重试、设备保留；登出撤销该会话设备。浏览器 `ui_e2e_push.py`：`/sw.js`（`Service-Worker-Allowed: /`）与 manifest 头正确、公钥可取、推送列与小节可见、manifest 已注入、Worker 注册成功、无头环境订阅失败时的提示。 |
| 发布与恢复 | schema v15 三张新表，无迁移；依赖新增 py-vapid、http-ece（锁文件已更新）。回滚到 v14 代码：表被忽略，订阅失效但无数据损失。生产：`SYNWEVO_MAIL_FROM` 已配置，VAPID `sub` 由此得到；如需关闭渠道设 `SYNWEVO_PUSH=off`。 |

## 2. 对象

| 表 | 用途 |
|---|---|
| `push_keys` | 部署的 VAPID 密钥对（PEM 私钥 + base64url 公钥），`status` active/retired |
| `push_subscriptions` | `user_id`、`session_id`、`endpoint`（唯一，不外泄）、`endpoint_hash`、`p256dh`、`auth`、`user_agent`、`last_sent_at`、`fail_count`、`last_error`、`revoked_at/revoked_reason`（removed / signed_out / gone / failing） |
| `push_queue` | 每条通知一行：pending → claimed → sent / skipped / failed；`not_before` 用于免打扰与退避 |

## 3. 接口（均需登录会话 + CSRF）

| 接口 | 说明 |
|---|---|
| `GET /api/auth/push/config` | `{enabled, public_key}` |
| `GET /api/auth/push/subscriptions?endpoint_hash_of=` | 我的设备（含 `this_device`） |
| `POST /api/auth/push/subscriptions` | `{subscription: PushSubscription.toJSON()}`；201；同端点再次提交即更新 |
| `DELETE /api/auth/push/subscriptions/{id}` | 移除 |
| `POST /api/auth/push/subscriptions/{id}/test` | 向该设备发一条测试；推送服务未接受 → 502 `push_failed` |
| `GET /sw.js`、`GET /manifest.webmanifest` | 由账户服务提供，`no-cache` |

Service Worker（`synwevo_identity/static/sw.js`）：`push` → `showNotification`（同一条通知以 `tag` 去重）；`notificationclick` → 聚焦已打开的对应页面或新开；
`pushsubscriptionchange` → 用同一公钥重新订阅并上报。不缓存、不拦截任何请求。

## 4. 明确未做

* VAPID 密钥轮换（需要让所有设备重新订阅的迁移流程）。
* 原生应用推送（无原生应用）；SMS / 第三方 IM。
* 多实例派发：派发器仍是账户角色单进程（与邮件一致）。
