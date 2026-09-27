# 域名迁移：synwevo.com 与 message.wegreen.ltd（2026-09-27）

产品的对外域名从 `*.wegreen.ltd` 迁到 `synwevo.com`；独立消息应用固定在 `https://message.wegreen.ltd`（明确例外，不改成
message.synwevo.com）。仓库里所有产品地址都来自配置（issuer、`SYNWEVO_PUBLIC_URL`、`SYNWEVO_APP_URL`、`SYNWEVO_PORTAL_URL`、
注册表 `launch_url`），代码不写死主机名；因此迁移是一次**配置切换 + 网关切换**，不是代码里的查找替换。盘点依据：
第九轮域名盘点（`docs/quality/verification.md` §16.69）。

## 1. 映射

| 用途 | 旧 | 新 | 说明 |
|---|---|---|---|
| 平台首页 / 工作空间 | `synwevo.wegreen.ltd` | `synwevo.com`（`/workspace`） | `SYNWEVO_PORTAL_URL`、门户 `APP_URL/PUBLIC_URL` |
| 账户服务 / OIDC / Passkey | `accounts.wegreen.ltd` | `accounts.synwevo.com` | `SYNWEVO_ISSUER`、账户 `PUBLIC_URL`、`SYNWEVO_RP_ID` |
| Synwevo Docs | `docwave.wegreen.ltd`（旧别名 `dw.wegreen.ltd`） | `docwave.synwevo.com` | Docs `APP_URL/PUBLIC_URL`，注册表 `launch_url` 由 Docs 进程启动时改写 |
| 工作空间别名 | — | `workplace.synwevo.com` | 网关 308 → `https://synwevo.com/workspace`（根）/ `https://synwevo.com$request_uri`（其它路径）。隧道已配置该主机名 |
| Synwevo Message | 账户源 `/messages` | `https://message.wegreen.ltd` | 独立应用（CI-66）；例外域名 |
| 发件地址 | `mail@hikosphere.com` | 不变 | 页面地址迁移 ≠ 邮箱迁移；换发件域需要 mailcow 邮箱、SPF/DKIM/DMARC 与 SMTP2GO 域验证，另立决定 |
| 第三方（Google/Microsoft 回调、Cloudflare、Git、用户内容外链） | — | 按各自用途处理 | 不做文本替换 |

## 2. 仓库侧（已在本仓库完成）

* `infra/production/compose.yaml`：issuer、门户地址、三个角色的公开地址、`SYNWEVO_RP_ID=accounts.synwevo.com`、网关健康检查 Host。
* `infra/production/nginx.conf`：新主机的 server 块（Docs 含 `/api/collab/` 的 Upgrade 块）、`workplace.synwevo.com` 别名、
  旧主机 `synwevo.wegreen.ltd` / `accounts.wegreen.ltd` / `docwave.wegreen.ltd` / `dw.wegreen.ltd` 的 **308** 永久重定向
  （保留路径与查询串；一次性凭证在 `#` 片段里，浏览器会重新附上；308 保留方法，仍指向旧主机的表单/API 调用被重发到新主机）。
  `nginx -t` 已在 `nginx:1.28-alpine` 里通过（上游名用 `--add-host` 解析）。
* `.github/workflows/production.yml`：部署后的公开健康检查主机来自仓库变量 `SYNWEVO_PUBLIC_HOSTS`（空格分隔），
  未设置时仍是旧三主机——切换当天由运维设为 `synwevo.com accounts.synwevo.com docwave.synwevo.com message.wegreen.ltd`，不需要再推代码。
* 口令黑名单加入 `synwevo.com`、`synwevomessage`（旧词保留：已有口令可能用到）。

## 3. 切换（运维，一次完成；一个发布窗口）

issuer 写进令牌并由三个依赖方校验，不能双跑；Cookie 只属于主机，旧主机上的登录态本来就不会带到新主机。因此切换是一个原子步骤：

1. **前置核对**（已见隧道配置：`synwevo.com`、`accounts.synwevo.com`、`docwave.synwevo.com`、`workplace.synwevo.com` → `http://127.0.0.1:18760`）：
   确认 `message.wegreen.ltd` 也已加入隧道公开主机名（同一目标）；Cloudflare 区域 Universal SSL 生效；`curl -I https://synwevo.com` 能到达网关
   （当前网关会因 `default_server 444` 断开——这正是切换前的预期）。
2. 在 VPS 用管理员通道安装本仓库的 `infra/production/compose.yaml` 与 `nginx.conf` 到 `/opt/synwevo/config/`（先 `nginx -t`），
   `runtime.env` 里若设置了 `SYNWEVO_TURNSTILE_HOSTNAMES`、`SYNWEVO_RP_ORIGINS` 一并改为新主机。
3. `docker compose exec accounts python -m synwevo_identity.cli client register --client-id portal-web --app platform --name 平台门户 --redirect https://synwevo.com/auth/callback --post-logout https://synwevo.com/signed-out --backchannel https://synwevo.com/auth/backchannel-logout`；
   同样为 `docweave-web`（`https://docwave.synwevo.com/...`）。旧 URI 可保留一段时间，不影响。
4. `docker compose up -d --wait accounts docs portal gateway`（重建三个角色与网关）；`app list` 核对 Docs `launch_url` 已变为 `https://docwave.synwevo.com`。
5. 设置 GitHub 仓库变量 `SYNWEVO_PUBLIC_HOSTS`。
6. 核对：三源 `/readyz`；旧主机 308；`workplace.synwevo.com/` → `/workspace`；受控账户在 `synwevo.com` 登录 → 工作空间 → Docs（SSO）→ 账户页「返回工作空间」；
   Passkey：旧凭证在账户页显示「不可在此域使用」，重新登记一枚；邮件里的链接以新主机生成（发一封验证/邀请邮件核对）。

## 4. 用户影响（如实说明，不掩盖）

| 影响 | 原因 | 处理 |
|---|---|---|
| 所有浏览器需要重新登录 | Cookie 只属于主机 | 旧主机 308 到新主机；登录后回到原目标 |
| 已注册的 Passkey 不能在新域使用 | RP ID 从 `accounts.wegreen.ltd` 变为 `accounts.synwevo.com`，WebAuthn 凭证按 RP ID 绑定，改域名字符串不能迁移凭证 | 账户页已有「域名变更」提示与重新登记路径；口令/邮箱验证码/外部登录不受影响。可选的 Related Origin Requests 需要长期保留旧主机且浏览器支持不齐，本轮不采用 |
| 浏览器推送需要重新开启 | 订阅属于旧源的 Service Worker | VAPID 密钥不变；旧源订阅按 404/410 自然失效 |
| 本机草稿、语言/主题偏好、上次返回目标 | localStorage/sessionStorage 按源隔离 | 一次性；不迁移 |
| 聊天里粘贴的旧域名 Docs 链接不再渲染成卡片 | 卡片只认当前注册表源 | 链接仍经 308 可打开；如需，`docs_links` 可加旧源白名单 |
| 切换窗口内进行中的登录会失败一次 | issuer 变化 | 重新登录即可 |

## 5. 回滚

重新安装切换前的 `compose.yaml` / `nginx.conf`（`/opt/synwevo/config` 的上一版本由运维保留），`up -d` 三角色与网关，仓库变量改回旧主机。
数据库不需要改动（客户端 URI 是并集、注册表地址随进程重写、通知目标按源名在读取时解析）。

## 6. 尚未由仓库完成、需要运维/持有人的事项

| 事项 | 状态 |
|---|---|
| `message.wegreen.ltd` 的隧道公开主机名 | 截图只见 4 条 synwevo.com 主机；请确认已加 |
| Google / Microsoft 控制台的新回调 URI（`https://accounts.synwevo.com/auth/external/<id>/callback`） | 生产尚未配置外部登录，配置时用新地址 |
| Turnstile 小部件主机名 | 生产尚未启用；启用时登记 `accounts.synwevo.com` |
| 发件域是否改为 synwevo.com | 决定项；改则先做 SPF/DKIM/DMARC 与 SMTP2GO 域验证 |
| 旧主机 308 保留期 | 建议 ≥ 90 天（已发邮件与书签） |
