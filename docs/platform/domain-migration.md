# 域名迁移：五个 synwevo.com 入口（2026-09-27 起，第十轮 2026-09-28 更新）

产品的对外域名从 `*.wegreen.ltd` 迁到 `synwevo.com` 及其子域。第十轮按用户最新截图确定五个正式入口：主站 `synwevo.com`、
账户 `accounts.synwevo.com`、Synwevo Docs `docwave.synwevo.com`、工作空间 `workplace.synwevo.com`、Synwevo Message
`message.synwevo.com`（取代第九轮的 `message.wegreen.ltd`，后者从未上线，只保留一条 308）。五个主机在隧道里都是路径 `*`
→ 来源服务 `http://127.0.0.1:18760`（网关），由网关按 Host 分给各自进程；主站与工作空间是同一个门户进程的两个主机，由门户按 Host 区分
（`SYNWEVO_SITE_URL` 与 `SYNWEVO_PORTAL_URL`）。`127.0.0.1:18760` 只是来源地址，不出现在任何对外链接里。
仓库里所有产品地址都来自配置（issuer、`SYNWEVO_PUBLIC_URL`、`SYNWEVO_APP_URL`、`SYNWEVO_PORTAL_URL`、`SYNWEVO_SITE_URL`、
注册表 `launch_url`），代码不写死主机名；因此迁移是一次**配置切换 + 网关切换**，不是代码里的查找替换。盘点依据：
第九轮域名盘点（`docs/quality/verification.md` §16.69）。

## 1. 映射

| 用途 | 旧 | 新 | 说明 |
|---|---|---|---|
| 平台首页（公开页、数据与隐私、服务说明） | `synwevo.wegreen.ltd` | `synwevo.com` | `SYNWEVO_SITE_URL`：canonical、分享摘要、sitemap 只用它 |
| 工作空间 | `synwevo.wegreen.ltd/workspace`、`synwevo.com/workspace`（第九轮） | `workplace.synwevo.com`（根即工作空间，`/workspace` 同样可用） | `SYNWEVO_PORTAL_URL`、门户 `APP_URL/PUBLIC_URL`：登录回到这里，所有「返回工作空间」指向这里；主站上的 `/workspace`、`/auth/start`、`/signed-out` 308 到这里（保留路径与查询），`/auth/callback` 不移（授权码只在签发时的地址兑换）；旧主机的 `/workspace` 一跳直达 |
| 账户服务 / OIDC / Passkey | `accounts.wegreen.ltd` | `accounts.synwevo.com` | `SYNWEVO_ISSUER`、账户 `PUBLIC_URL`、`SYNWEVO_RP_ID` |
| Synwevo Docs | `docwave.wegreen.ltd`（旧别名 `dw.wegreen.ltd`） | `docwave.synwevo.com` | Docs `APP_URL/PUBLIC_URL`，注册表 `launch_url` 由 Docs 进程启动时改写 |
| Synwevo Message | 账户源 `/messages`（未注册时的兜底外壳）；`message.wegreen.ltd`（从未上线） | `https://message.synwevo.com` | 独立应用（CI-66）；`message.wegreen.ltd` 308 → 新主机 |
| 发件地址 | `mail@hikosphere.com` | 不变 | 页面地址迁移 ≠ 邮箱迁移；换发件域需要 mailcow 邮箱、SPF/DKIM/DMARC 与 SMTP2GO 域验证，另立决定 |
| 第三方（Google/Microsoft 回调、Cloudflare、Git、用户内容外链） | — | 按各自用途处理 | 不做文本替换 |

## 2. 仓库侧（已在本仓库完成）

* `infra/production/compose.yaml`：issuer、门户地址、三个角色的公开地址、`SYNWEVO_RP_ID=accounts.synwevo.com`、网关健康检查 Host。
* `infra/production/nginx.conf`：新主机的 server 块（Docs 含 `/api/collab/` 的 Upgrade 块）、`workplace.synwevo.com` 别名、
  旧主机 `synwevo.wegreen.ltd` / `accounts.wegreen.ltd` / `docwave.wegreen.ltd` / `dw.wegreen.ltd` 的 **308** 永久重定向
  （保留路径与查询串；一次性凭证在 `#` 片段里，浏览器会重新附上；308 保留方法，仍指向旧主机的表单/API 调用被重发到新主机）。
  `nginx -t` 已在 `nginx:1.28-alpine` 里通过（上游名用 `--add-host` 解析）。
* `.github/workflows/production.yml`：部署后的公开健康检查主机来自仓库变量 `SYNWEVO_PUBLIC_HOSTS`（空格分隔），
  未设置时仍是旧三主机——现设为 `synwevo.com workplace.synwevo.com accounts.synwevo.com docwave.synwevo.com message.synwevo.com`，不需要再推代码。
* 口令黑名单加入 `synwevo.com`、`synwevomessage`（旧词保留：已有口令可能用到）。

## 3. 切换（运维，一次完成；一个发布窗口）

> **已于 2026-09-27 16:39 UTC 完成**（记录：`docs/quality/deployment.md`）。实际做法与下文一致，只有两点不同：compose 用的是主机上现有文件只改七个主机值（主机尚未采用 ClamAV 服务，那是另一个决定）；`client register` 会**整体替换** URI 列表，所以新旧回调都列出。切换脚本要以文件方式在主机上执行并把 `docker compose exec` 的 stdin 接到 `/dev/null`——用 `bash -s` 经 stdin 喂脚本会被第一条 `exec` 吃掉后半段。

issuer 写进令牌并由三个依赖方校验，不能双跑；Cookie 只属于主机，旧主机上的登录态本来就不会带到新主机。因此切换是一个原子步骤：

1. **前置核对**（已见隧道配置：`synwevo.com`、`accounts.synwevo.com`、`docwave.synwevo.com`、`workplace.synwevo.com` → `http://127.0.0.1:18760`）：
   确认 `message.synwevo.com` 也已加入隧道公开主机名（同一目标）；Cloudflare 区域 Universal SSL 生效；`curl -I https://synwevo.com` 能到达网关
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
| 已注册的 Passkey 不能在新域使用 | RP ID 从 `accounts.wegreen.ltd` 变为 `accounts.synwevo.com`，WebAuthn 凭证按 RP ID 绑定，改域名字符串不能迁移凭证 | 账户页已有「域名变更」提示与重新登记路径；口令/邮箱验证码/外部登录不受影响。登录页的 Passkey 登录是可发现凭证（匿名，不列凭证以免枚举账户），切换后浏览器会报「没有可用凭证」——公开状态多一个全部署布尔值 `passkeys_elsewhere`（库里存在绑定到其它 RP ID 的有效 Passkey），登录页据此把「已取消或没有可用密钥」改成解释域名已换、请用口令/验证码登录后到「账户 → 安全」重新登记（CI-70）。可选的 Related Origin Requests 需要长期保留旧主机且浏览器支持不齐，本轮不采用 |
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
| `message.synwevo.com` 的隧道公开主机名 → 网关 `http://127.0.0.1:18760` | 第十轮已由持有人加入（2026-09-28 探测：请求到达网关，未配置时网关答 444 → 边缘 502）；消息服务按下文上线 |
| `dw.wegreen.ltd` 旧别名 | 不是隧道主机名（边缘 TLS 握手失败，切换前亦然）；网关里的 308 仅在有人把它加入隧道时生效 |
| 主机 compose 采用 ClamAV 服务（`SYNWEVO_MALWARE_SCAN=required`） | 仓库参考文件含该服务，主机文件未采用；主机可用内存约 4.5 GiB，采用与否是持有人决定（消息服务的附件扫描同样依赖它：未采用时把 `messages` 服务的两项扫描变量与 accounts/docs 保持一致） |
| `compose.yaml` 加 `messages` 服务（角色 `messages`，`SYNWEVO_APP_URL/PUBLIC_URL=https://message.synwevo.com`，`SYNWEVO_OIDC_CLIENT_ID=messages-web`，`SYNWEVO_OIDC_APP_ID=messages`，clamd 两项；网关 `depends_on` 加它） | 仓库参考文件已含（CI-66）；安装到 `/opt/synwevo/config/` 是运维步骤 |
| `nginx.conf` 加 `message.synwevo.com` 的 server 块（`/api/messaging/stream` 长读超时） | 同上；`nginx -t` 后 reload |
| `docker compose exec accounts python -m synwevo_identity.cli client register --client-id messages-web --app messages --name "Synwevo Message" --redirect https://message.synwevo.com/auth/callback --post-logout https://message.synwevo.com/signed-out --backchannel https://message.synwevo.com/auth/backchannel-logout` | 首次上线前执行一次（幂等） |
| 注册表行 `apps.id='messages'`（`launch_url=https://message.synwevo.com/`） | 由 `messages` 进程**首次启动**写入；在此之前账户源仍以旧前缀回答会话接口（`/messages` 页面已不在账户外壳里），三个源的「消息」链接回退到账户源 `/messages`；启动后自动切换为 303/410，无需重启其它角色 |
| `receive.py` 的 `SERVICES` 含 `messages`（随本仓库安装到 VPS 的 receiver 才会滚动它） | 仓库已改；receiver 另行安装（README） |
| 仓库变量 `SYNWEVO_PUBLIC_HOSTS` 加 `message.synwevo.com` | 主域切换时已设三主机；消息服务上线时补上第四个 |
| Google / Microsoft 控制台的新回调 URI（`https://accounts.synwevo.com/auth/external/<id>/callback`） | 生产尚未配置外部登录，配置时用新地址 |
| Turnstile 小部件主机名 | 生产尚未启用；启用时登记 `accounts.synwevo.com` |
| 发件域是否改为 synwevo.com | 决定项；改则先做 SPF/DKIM/DMARC 与 SMTP2GO 域验证 |
| 旧主机 308 保留期 | 建议 ≥ 90 天（已发邮件与书签） |
