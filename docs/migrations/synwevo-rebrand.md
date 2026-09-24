# Synwevo 品牌统一与仓库迁移

平台的正式名称定为 **Synwevo**。这一轮把名字落到代码、目录、包、配置和文档里，并把主要 Git
remote 切到 `git@github.com:WeGreen-AI/Synwevo.git`。

改品牌最容易犯的错误是**把搜索结果清零当成目标**。有些字符串看着是旧品牌，实际上是别人系统里
的主键：应用 ID 写在已经签发的令牌里，客户端 ID 写在登记过的回调白名单里，Cookie 名字写在
每一个还开着的标签页里。把它们一起改掉，换来的是"搜不到 docweave 了"，代价是别人的账号、
Passkey 和定时任务同时失效。所以下面分成三类：**改**、**兼容迁移**、**明确保留**。

---

## 1. 名称映射

| 对象 | 迁移前 | 迁移后 |
|---|---|---|
| 平台与主品牌 | （未定，占位「工作平台」） | **Synwevo** |
| 平台首页 / 工作空间 | 平台门户 | **Synwevo Workspace**（导航文案仍可写"首页""工作空间"） |
| 统一账户体系 | 统一账户服务 | **Synwevo Account**（导航文案仍可写"账户""安全"） |
| 文档应用对外名称 | Synwevo Docs | **Synwevo Docs** |
| 品牌标记（页面左上角字母） | `W` | `S` |

## 2. 路径映射

| 迁移前 | 迁移后 |
|---|---|
| `/Users/lihiko/repo/wegreen/platform-workspace` | `/Users/lihiko/repo/wegreen/synwevo` |
| `apps/docweave/` | `apps/docs/` |
| `apps/docweave/docweave/` | `apps/docs/synwevo_docs/` |
| `packages/platform-core/platform_core/` | `packages/core/synwevo_core/` |
| `packages/auth-client/python/platform_auth_client/` | `packages/auth-client/python/synwevo_auth_client/` |
| `services/identity/platform_identity/` | `services/identity/synwevo_identity/` |
| `apps/portal/platform_portal/` | `apps/portal/synwevo_portal/` |
| `infra/server/platform_server/` | `infra/server/synwevo_server/` |

全部用 `git mv` 完成，`git show --stat` 里逐个识别为重命名；纯移动单独一个提交，导入与逻辑
修复在下一个提交里，和上一轮结构重组同样的做法。

## 3. 包与命令

| 类型 | 迁移前 | 迁移后 |
|---|---|---|
| Python 发行版 | `platform-core` / `platform-auth-client` / `platform-identity` / `platform-portal` / `platform-server` / `docweave` | `synwevo-core` / `synwevo-auth-client` / `synwevo-identity` / `synwevo-portal` / `synwevo-server` / `synwevo-docs` |
| Python 导入名 | `platform_core` … `docweave` | `synwevo_core` … `synwevo_docs` |
| npm 工作区 | `platform-workspace` | `synwevo` |
| npm 包 | `@platform/ui`、`@platform/auth-client`、`@docweave/web`、`@platform/portal-web`、`@platform/accounts-web` | `@synwevo/ui`、`@synwevo/auth-client`、`@synwevo/docs-web`、`@synwevo/workspace-web`、`@synwevo/account-web` |
| CLI | `python -m docweave.cli` | `python -m synwevo_docs.cli` |
| CLI | `python -m platform_identity.cli` | `python -m synwevo_identity.cli` |
| 运行入口 | `python -m platform_server.run` | `python -m synwevo_server.run` |
| 运行器服务键 | `platform.py up docweave` | `platform.py up docs`（`logs` / `test` 同） |

`@synwevo` 是**仓库内部的命名约定**，不代表已经拥有 npm 注册表的这个 scope，本轮也没有发布
任何包。Python 发行版同理：只在本机以可编辑方式安装，未上传任何索引。

**没有为 Python 模块名保留兼容入口。** 这些命名空间是上一轮（2026-09-23 结构重组）才引入的，
只在本仓库的文档里出现过，没有外部调用方；留一个转发包换来的是两套入口和一份要同步维护的
文档。真正有外部使用者的兼容面是环境变量和稳定标识，见 §4 与 §5。

## 4. 配置：兼容迁移

新名字分两个作用域，**依据是谁在读它**，不是前缀好不好看：

| 作用域 | 前缀 | 判断标准 |
|---|---|---|
| 平台级 | `SYNWEVO_*` | 账户服务、门户、依赖方适配、组装层或共享层读它 |
| 文档应用级 | `SYNWEVO_DOCS_*` | 只有 Synwevo Docs 自己读它（PDF 引擎、OCR、任务上限、Webhook…） |

实现在 `packages/core/synwevo_core/env.py`，读取规则：

| 情况 | 行为 |
|---|---|
| 只设了新名字 | 用新的 |
| 只设了旧名字 `DOCWEAVE_*` | **仍然可用**，每个变量在进程生命周期里提示一次 |
| 两个都设了、值相同 | 用它，不提示 |
| 两个都设了、值不同、普通项 | 用新的，并提示不一致 |
| 两个都设了、值不同、**关键项** | **拒绝启动**，报出两个名字和两个值 |

关键项是指向数据、密钥、信任边界或身份地址的那些：`DATA_DIR`、`SECRET`、`ISSUER`、`UI_AUTH`、
`ROLE`、`RP_ID`、`RP_ORIGINS`、`COOKIE_SECURE`、`TRUST_PROXY`、`APP_URL`、`PUBLIC_URL`、
`PORTAL_URL`、`OIDC_CLIENT_ID`、`OIDC_APP_ID`、`SMTP_PASSWORD`、`WEBHOOK_SECRET`、`API_KEY`、
`IMPORT_ROOTS`、`UI_DIR`、`TESSDATA` 等（完整集合见 `env.CRITICAL`）。

拒绝而不是挑一个，是因为 `DOCWEAVE_DATA_DIR=/srv/a` 与 `SYNWEVO_DATA_DIR=/srv/b` 同时存在时，
任何优先级规则都是在猜用户的数据在哪儿。猜错的那一次不会报错：SQLite 会在错的路径上建一个空库，
服务正常启动——而那正是"数据不见了"的样子。这种冲突必须由人解决。

几个被重命名的运行器变量（它们本来就不带 `DOCWEAVE_` 前缀）：

| 迁移前 | 迁移后 |
|---|---|
| `PLATFORM_ACCOUNTS_PORT` / `PLATFORM_PORTAL_PORT` | `SYNWEVO_ACCOUNTS_PORT` / `SYNWEVO_PORTAL_PORT` |
| `PLATFORM_DOCWEAVE_PORT` | `SYNWEVO_DOCS_PORT`（旧名仍被 `scripts/platform.py` 接受） |
| `PLATFORM_ACCOUNTS_UI_DIR` / `PLATFORM_PORTAL_UI_DIR` | `SYNWEVO_ACCOUNTS_UI_DIR` / `SYNWEVO_PORTAL_UI_DIR` |

## 5. 明确保留的旧标识

这些**没有改**，因为它们不是品牌文案，是别人系统里的键：

| 标识 | 值 | 为什么保留 | 后续迁移的条件 |
|---|---|---|---|
| `app_id` | `docweave` | `apps` / `workspace_apps` / `app_entitlements` 的主键，也写在已签发的令牌里 | 需要一次带数据迁移的联动改动：改表 + 重签所有令牌 + 通知所有机器调用方 |
| `client_id` | `docweave-web` | 登记在 `oauth_clients` 里，与回调白名单绑定 | 同上；登记一个新客户端并双写一段时间更安全 |
| API scope | `docweave.read` / `docweave.write` | 写在已签发的访问令牌的 `scope` 里 | 需要同时接受新旧 scope 一个刷新周期 |
| 本机域名 | `docweave.localhost:8765` | 注册过的 OIDC 回调、Passkey 的可信 origin、Cookie 作用域 | 改域名会使已登记的 Passkey 失效（RP ID 绑定域名），见 `docs/security/sessions.md` |
| Cookie 名 | `dw_session` / `dw_csrf` / `dw_authreq` | 改名等于让所有在线会话在部署瞬间失效 | 可做"同时读两个名字、只写新名字"的滚动迁移 |
| API 密钥前缀 | `dw_` | 已经发出去的密钥字面量里就有它 | 只能在签发新密钥时改，且要同时接受两种前缀 |
| 数据库文件名 | `data/docweave.sqlite3` | 改名是一次数据迁移，收益只有"看起来一致" | 备份 → 改名 → 核验 → 回退路径，需要单独一轮 |
| 口令黑名单词 | `docweave` | 仍然是与本服务相关的词，不该被用作口令 | 不迁移；**另外加上了** `synwevo` 等新词 |

### 差一点被改掉的两个字节串

`synwevo_identity/mfa.py` 里有两个**密钥派生的域分隔标签**：

```
b"docweave/mfa-seed/v1"        # TOTP 种子加密密钥的派生标签
 "docweave/recovery/v1:"       # 恢复码摘要的前缀
```

它们长得像品牌文案，实际上是密码学参数。把它们跟着品牌改掉，派生出来的就是另一把密钥、另一个
摘要：库里所有已加密的 TOTP 种子都解不开，所有已发出的恢复码都对不上。表现是**每个人的两步
验证在部署当天同时失效**，而且没有任何报错会指向"改了品牌"这件事。

代码里现在有注释写明这一点，`scripts/check_brand.py` 里也有对应的保留理由。

### 两处做了滚动兼容

| 位置 | 新 | 旧 | 规则 |
|---|---|---|---|
| CSRF 请求头 | `X-Synwevo-CSRF` | `X-DocWeave-CSRF` | 页面只发新的；**服务端两个都接受**。部署不是对所有已打开的标签页原子生效的，拒绝旧头会让那些人的每一次写操作都失败到刷新为止 |
| 浏览器本地存储前缀 | `synwevo.*` | `docweave.*` | 读时新键优先，缺失则读旧键并**顺手写到新键**；旧键不删，所以回退到上一个版本仍能读到自己的偏好 |

### 残留审计是可复现的

`python scripts/check_brand.py` 把每一处 `docweave` 归到一个**有名字的保留理由**，剩下的就是漏改，
退出码非零。它不数次数——数次数会让"删掉一段必要的兼容代码"看起来像进展。

本轮结果：全部归类，0 处未分类。最大的几类是 OIDC client_id（66）、app_id 值（57+20）、
本机主机名（54）、数据库文件名（29）、API scope（29）、旧环境变量名（22）。

## 6. Git remote 切换

| 项 | 值 |
|---|---|
| 迁移前 fetch/push | `git@github.com:WeGreen-AI/DocWeave.git`（两者相同，无独立 `pushurl`） |
| 迁移后 fetch | `git@github.com:WeGreen-AI/Synwevo.git` |
| 迁移后 push | `git@github.com:WeGreen-AI/Synwevo.git` |
| `remote.origin.pushurl` | 未设置（检查过） |
| `remote.pushDefault` | 本地与全局均未设置（检查过） |
| `url.*.insteadOf` 重写 | 无（检查过） |
| 分支级 push remote | 无（只有 `branch.<name>.remote = origin`） |

**目标仓库状态（只读探测，`git ls-remote`）**：退出码 0，**零个引用** —— 仓库存在、当前 SSH 身份
（`HikoPLi`）可访问、且**是空的**（还没有任何提交）。

**旧仓库**：`git ls-remote git@github.com:WeGreen-AI/DocWeave.git` 返回
`ERROR: Repository not found.`（退出码 128）。这可能是已改名、已删除，或当前身份不再有权限——
从这一条输出**无法区分**是哪一种，所以这里只记录现象，不下结论。

**旧的远端跟踪引用没有被当成新远端的事实。** `refs/remotes/origin/upgrade/automation-api-ui`
（指向 `e292a3e`）已移到 `refs/docweave-origin/upgrade/automation-api-ui` 保留为历史依据，
并从 `refs/remotes/origin/` 下移除。因为目标仓库是空的，`git fetch origin` 成功但不产生任何
跟踪引用，所以 `@{upstream}` 目前**无法解析**——这是真实状态，没有伪造一个远端分支。

**本轮没有推送。** 没有 `push`、没有 `--mirror`、没有强推、没有远端删除，也没有创建、改名或
删除任何 GitHub 仓库。

### 建议的后续推送（由你决定何时执行）

```bash
git push -u origin upgrade/automation-api-ui     # 建立 upstream，推送当前分支
git push origin main                              # 如果也要这条分支
```

目标是空仓库，所以第一次推送不会覆盖任何东西，也不需要 `--force`。推送之后
`@{upstream}` 才会变得可解析。

## 7. 运行数据与身份的保全

目录改名**没有**隐式切换任何数据：

| 项 | 结果 |
|---|---|
| 数据库 | 随仓库根目录改名一起移动，位置仍是仓库根的 `data/docweave.sqlite3`；44 张表逐表行数与迁移前备份一致 |
| blob | 695 个文件，摘要清单与迁移前逐字节一致 |
| `default` 工作空间 | 仍是 `legacy / pending_claim / 0 成员`，未因新品牌重新初始化 |
| 密钥 | 服务端根密钥与 OIDC 签名密钥都在库内，未重新生成 |
| issuer / client_id / audience / workspace / 角色 / 对象 ID | 全部未变 |
| Passkey RP ID 与可信 origin | 未变 |
| 应用展示名 | 代码里已改为 `Synwevo Docs`；`register()` 在每次启动时幂等更新 `apps.name` 这一列。**真实库里目前仍是 `DocWeave`**，因为本轮没有任何服务打开过真实数据目录（所有运行验证都在隔离实例上）。下一次对着真实 `data/` 启动任一角色时自动更新，不需要手工 SQL |
| 用户内容 | 项目名、文件名、模板、历史产物、审计记录**一律未改**，即使其中出现旧名字 |

备份：`/Users/lihiko/repo/wegreen/.synwevo-rebrand-backup-20260924/`
（`git bundle --all` + SQLite 备份 API 副本 + 695 个 blob + 逐表行数清单）。

## 8. 回退

| 想回到 | 怎么做 |
|---|---|
| 改名前的代码 | `git checkout 39948e0`（本轮第一个提交之前），历史完整可达 |
| 改名前的目录名 | `mv synwevo platform-workspace`；`.git` 是普通目录，改名无副作用 |
| 改名前的 remote | `git remote set-url origin git@github.com:WeGreen-AI/DocWeave.git`；旧跟踪引用还在 `refs/docweave-origin/` |
| 改名前的数据 | §7 的备份目录；**Git 回退不等于业务数据恢复**，两件事分开准备 |
| 仍然用旧环境变量 | 不用做任何事：`DOCWEAVE_*` 继续被接受（§4） |
