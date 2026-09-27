# 进度记录（用于接续）

## 2026-09-26 邮件设计系统与 Docs 实时协同轮（Claude Code，本机）

| 项 | 结果 |
|---|---|
| CI-43 正式 Logo 上线 | 已部署（`30a5cfd`，run 36212722189）；生产三源 favicon 与带指纹图标 200（deployment.md）。 |
| CI-44 系统邮件 | 八类账户邮件 + 通知邮件统一布局（文本 + HTML 同源、三语言、Logo）；已部署（`e04ff01`，run 36214183403），`/brand/email-logo.png` 生产 200。真实邮箱客户端渲染待授权收件箱。 |
| CI-47 表格协同 | 表格型模板：在场与跳转、单元格/字段/行列级三方合并（同一对象双方都改才冲突，可选保留）、他人保存即时载入；4 项 pytest、Chromium/WebKit 两账户各 5 里程碑（verification.md §16.48）。已部署（随 CI-48，`cb2090e`）。 |
| CI-48 发布链路 | CI-47 的生产运行被 45 分钟上限取消的根因（每个测试的应用泄漏后台线程，verify 容器 3 GiB 耗尽）已修复：worker 登记表、lifespan 退出钩子（生产优雅退出同时写出实时文档）、conftest 兜底、CI 容器 `--init`；三套件 2110 通过且结束只剩主线程（verification.md §16.49）。已部署（`cb2090e`，run 36249289199）：CI verify 容器步骤通过、三源 readyz 就绪；CI-47 随之进入生产。 |
| CI-49 版本操作与实时修改 | 恢复/撤销/重做不再无声丢掉房间里别人未保存的输入：服务端 409 `live_edits` 点名在线者/最后修改者，`discard_live` 才丢弃（浏览器、机器 API、SDK、CLI 同契约）；版本对话框先说明再确认、快捷撤销只拒绝并指路；手机提示栈最多两条。pytest +2，Chromium/WebKit 各 5 里程碑（verification.md §16.50）。已部署（`ba539af`，run 36250980928）。 |
| CI-50 可追溯 | 活动记录：成员/对象筛选、「上次查看后 N 条」与分割线、整行跳到对象（文件/映射/评论）；评论增/回复/改/解决/重开/删经项目房间实时到达并进活动记录（只存 id）。pytest +1，Chromium/WebKit 各 5 里程碑（verification.md §16.51）。已部署（`b46a403`，run 36252593410）。 |
| CI-51 重启不丢 | 优雅关闭写出实时文档、新进程从草稿播种、客户端重连带离线输入汇合；pytest +1，Chromium/WebKit 真实重启各 3 里程碑（verification.md §16.52）。已部署（`b46a403`）。 |
| CI-52 命名检查点 | 保存体可选 `note`（≤200 字），两个编辑器的说明输入框，SDK/CLI 同契约，历史显示；pytest +1，Chromium/WebKit 各 4 里程碑（verification.md §16.53）。已部署（`b46a403`）。 |
| CI-53 变更差异 | 变更记录每行显示块内的插入与删除（schema v22 加 `before` 列，前端逐码点差异）；pytest +1，前端 4 项，Chromium/WebKit 各 4 里程碑（verification.md §16.55）。已部署（`11202c3`，run 36253921338）。 |
| CI-54 版本对话框 | 恢复前可展开看到尚未保存为版本的实时修改（谁、改了哪块、插入/删除），与编辑器变更记录同源渲染（verification.md §16.56）。已部署（`11202c3`）。 |
| CI-55 有界与恢复 | 入站帧 2 MiB 上限（超限只关该连接，客户端不重试并说明）；损坏草稿不再锁死房间（改用 head 版本并提示）；远端可见延迟测量（回环 1–8 ms）。pytest +2（verification.md §16.57）。已部署（`1d15665`，run 36255489490）。 |
| CI-56 设置前后值 | 活动记录把项目设置变更读成「样本规则：未设置 → 每个文件一份」这样的前后对比（verification.md §16.58）。已部署（`1d15665`）。 |
| CI-57 里程碑回归 | Docs 桌面/手机主链路证明重新通过；无账户部署模式下模板选择器不再被送去不存在的登录页（verification.md §16.59）。已部署（`e51ceb0`，run 36304552064）。 |
| CI-58 登录落点 | 用户反馈：账户服务登录后停在 `/account`。无明确目标的登录与账户服务根路径现在经门户 `/auth/start` 落到平台工作空间；显式目标、验证/邀请/引导、无门户部署的行为不变（platform.md §8，verification.md §16.60）。已部署（`ad2eeeb`，run 36313988930）；线上只读核对新块与 `portal_url` 已上线，行为核对待账户持有人下次登录。 |
| CI-59 退出落点 | 退出登录时被守卫页面的「去登录」重定向与 `/logged-out` 导航竞态，Chromium 落到登录页；现在退出中不再重定向，两引擎都到「已退出」页（verification.md §16.61）。已部署（`8184dc2`，run 36315903038）。 |
| CI-60 依赖方退出 | Docs 的「退出登录」此前只退本应用、页面却宣称平台已退出；现在经账户服务登出结束平台会话并回到 Docs/门户自己的「已退出」页（verification.md §16.62）。已部署（`33c3e1f`，run 36318459107）。 |
| CI-61 门户退出入口 | 门户页头/账户菜单新增「退出登录」，走平台级退出并回到门户自己的「已退出」页（verification.md §16.63）。已部署（`e900774`，run 36320325701）。 |
| CI-62 返回平台链接 | Docs 的「← Synwevo」与账户外壳品牌链接从公开首页改为工作空间（verification.md §16.65）。已部署（`41e6f4f`，run 36323605133）。 |
| CI-63 门户消息入口 | 门户页头铃铛旁新增「消息」入口与未读徽标（verification.md §16.66）。 |
| CI-64 未读徽标跨源 | 拆分部署里门户/Docs 的消息未读数曾一直为空（路由只在账户服务）；现由共享会话路由在每个源回答（verification.md §16.67）。 |
| CI-65 Accounts 返回导航 | 顶栏常驻「返回工作空间」（门户交接、恢复当前空间），子页面按路径显示父级链接，组织页改用账户外壳（verification.md §16.68）。 |
| CI-67 域名迁移准备 | 新主机 compose/nginx 参考与旧主机 308、别名、CI 健康检查变量、运行手册（docs/platform/domain-migration.md）；线上切换是运维一次性步骤。 |
| CI-68 私信邮件规则 | 私信邮件缺省关、开启后只有未读摘要（30 分钟一封、每天 8 封），寄出前复核已读/撤回/静音/成员资格，正文默认不带消息原文；旧偏好行迁移重置（verification.md §16.70）。 |
| CI-69 Docs 文案精简 | Docs 外壳 60 条改写、109 条死 key 删除、术语统一，规则与前后对比在 docs/platform/docs-copy.md（verification.md §16.71）。 |
| CI-70 Passkey 域名切换解释 | 登录页在旧域名 Passkey 不可用时说明原因与重新登记路径，依据全部署布尔值 `passkeys_elsewhere`（verification.md §16.72）。 |
| CI-71 公开页面 | 首页围绕品牌含义重写，两个真实应用、协作方式、数据与隐私摘要、下一步；新增数据与隐私、服务说明正式页面（事实有来源，运行时事实读自服务）；门户术语统一（verification.md §16.73）。 |
| CI-76 账户文案 | 账户外壳全部字符串按规则重写、术语统一、重复组件删除，记录在 docs/platform/account-copy.md（verification.md §16.75）。 |
| CI-75 Docs ↔ Message 联动 | 项目（与其任务）、复制申请各有一个按对象授权维护成员的对象会话；Docs 的评论、任务结束、复制申请决定与结果在同一事务写外发表，消息进程的 worker 幂等地投递成由应用身份 Synwevo Docs 发出的卡片，每个读者只看到自己有权看的内容，卡片不产生收件箱条目；Docs 项目页与任务页新增「在消息中讨论」；记录在 messaging.md §8，验证在 verification.md §16.77。已实现（worktree），待合并、外壳路由与卡片渲染由外壳批次实现。 |
| CI-74 消息服务端能力 | 在线/输入、可选回执、标为未读、转发、撤回与仅自己删除、语音、FTS5 搜索、事件 id/补发/`sync`、指标；契约 docs/platform/messaging.md §7（verification.md §16.76）。 |
| CI-73 消息外壳 | 聊天界面重建（布局、气泡、引用、回应、菜单、历史锚定、新消息提示、编辑器、附件、搜索定位、手机三段式、动效与无障碍）并接入设置/在线/输入/回执/撤回/转发/语音/搜索/resync/卡片；消息源允许麦克风（verification.md §16.78）。 |
| CI-66 Synwevo Message 独立源站 | 消息成为第四个应用 `apps/messages`（角色 `messages`，`/api/messaging`，本机 `message.localhost:8768`，生产 `message.wegreen.ltd`）；业务层与表原样在 core；注册表行驱动三个源的「消息」链接（`state.messages_url`）、门户卡片与会话通知目标；账户源旧路径 410 / `/messages` 303；举报处理留在账户服务；读到会话末尾时收件箱条目标为已读（verification.md §16.74）。已实现（本机四源隔离实例实测），待部署；运维项见 domain-migration.md §6。 |
| 本轮暂停点 | 用户要求本轮完成后暂停（2026-09-26 05:30 UTC）。接续：运维在生产主机同步 nginx 的 Upgrade 配置并 reload（deployment.md）；`SheetEditor` 协同；真实邮箱客户端渲染（需授权收件箱）；本条记录提交在本地、未推送，下一批推送时一并带上。 |
| CI-46 项目页协同 | 在场与定位、按映射三方合并（冲突才 409）、他人变更即时提示、归属活动记录；6 项 pytest、Chromium/WebKit 两账户各 7 里程碑（verification.md §16.47）。已部署（`e6197c1`，run 36219721565）。 |
| CI-45 实时协同 | 文档型模板的多人共同编辑：服务端房间（pycrdt）、命名光标、在线名单、逐块归属、草稿持久化、只读与单人回退；12 项 pytest、Chromium/WebKit 两账户证明（verification.md §16.46）。已部署（`c2d78d0`，run 36218166471）；生产 nginx 副本需运维加 Upgrade 头后实时协同才在线上生效，此前编辑器自动退回单人模式。 |

## 2026-09-26 正式 Logo 接入轮（Claude Code，本机）

三张确认 Logo（完整版 / 图形版 / 字标版）接入全部平台自有品牌位。契约在 [design.md](../design.md) §2.5，
衍生文件由 `scripts/brand_assets.py` 从 `packages/ui/brand/originals/` 生成并由 `--check` 守护（CI 已接入）。

| 项 | 结果 |
|---|---|
| 原图识别 | 按像素识别：`1.png` 完整版 1942×809、`2.webp` 字标 2000×667、`3.png` 图形 1254×1254，均为真实透明背景（墨色 alpha 251–254）。独立图形/字标与完整版不一致（IoU 0.83 / 0.78），以完整版为准无损裁出 |
| 接入位置 | 三个外壳顶栏与状态页（完整版，窄屏按各自断点换图形版或去掉产品名）、登录/注册框架与账户恢复卡片（完整版）、门户页脚/登录框架页脚/首页流程示意卡标题（字标版）、整页载入/跳转/错误状态 `PageState`（图形版）、三个外壳的 favicon 与 apple-touch-icon（图形版）、README 页首 |
| 刻意不放 | 卡片、对话框、按钮、组织/用户头像、外部身份提供商标记、用户文档/模板/生成产物、纯文本邮件、CSV 报表；外壳只有浅色主题，界面不用白色版本；无 PWA/manifest |
| 浏览器验证 | Chrome（Playwright）隔离实例 `instance=test`：41 个页面/状态 × 1440/375/320，签入与未签入、强制色、系统深色、路由与会话载入态；0 个问题。首页头、登录、状态页、Docs/账户顶栏手机宽度无溢出、无裁切 |
| 测试与检查 | 前端 typecheck/build 三外壳通过；UI 19、auth-client 31、Docs 45、账户外壳 19 项通过（新增 `brandLinkName` 3 项）；`check_boundaries` / `check_brand` / `check_design` / `brand_assets.py --check` 通过；集成测试新增各角色 favicon 与 /favicon.ico 断言（含单进程角色）。Python 全量在同一工作区跑了三次（并行会话在同时修改身份服务）：2006/1 skipped/0 failed；2012/1 skipped/1 failed；2012/1 skipped/1 failed。两次各 1 项失败都在并行会话半写的代码上（`external.py` 的 `_notify`、`Organizations.require()` 的签名），与 Logo 无关；对应测试文件在其修正后重跑通过（28 passed、3 passed） |
| 未做 | 未推送、未部署；PWA/manifest 不存在故无 maskable 图标；邮件为纯文本不放图片；Vite 开发服务器的图标由 `@synwevo/ui/vite` 插件提供（已用 curl 验证） |

最后更新：2026-09-25（导航稳定、弹层与移动端轮，Claude Code）。本轮八条反馈的复现—根因—修复—验证表在
[issues.md](issues.md)「导航、弹层与移动端轮」；接续点（Google/Microsoft 登记、生产管理员本人绑定 TOTP、真机/真实邮箱/真实样本）
在 [handoff.md](handoff.md) 顶部；浏览器与测试证据在 [verification.md](verification.md) §16；契约在 [sessions.md](../security/sessions.md) §4.6、§12 与
[permissions.md](../security/permissions.md)。`a342994` 与 `61f6354` 已依次经 CI 验证并部署（备份 `20260925T071558Z`、`20260925T073728Z`），
线上匿名检查见 [deployment.md](deployment.md)；线上未做登录态流程验收。

上一轮（统一白蓝设计系统与多种登录方式轮，Claude Code）：本轮实现、证据与边界以 [handoff.md](handoff.md) 顶部一节为准；
CI/CD 与线上状态以 [deployment.md](deployment.md) 为准。设计系统契约在 [design.md](../design.md)，守卫为 `scripts/check_design.py`。

最后更新：2026-09-25。本轮本机账户/组织/API 台/三语言产品已完成关键流程集成验收；具体实现、通过/失败/跳过记录及未验证边界以 [handoff.md](handoff.md) 为单一状态。
用户随后明确授权部署。平台首页已改为工作流程自动化主题，Docs 为独立应用区；`main` 代码 `1a48359` 已通过 CI 并部署，线上开放注册生效。
CI/CD、备份和线上验证的当前状态统一记录在 [deployment.md](deployment.md)，不把本机验收当作线上验收。

## 下方为历史进度（不代表当前轮验收）

## 历史位置（2026-09-24）

| 项 | 值 |
|---|---|
| 仓库根目录 | `/Users/lihiko/repo/wegreen/synwevo`（2026-09-23 由 `.../DocWeave` 改名为 `platform-workspace`，2026-09-24 再改名为 `synwevo`；Git 历史原样保留） |
| 分支 | `main`，跟踪 `origin/main` |
| 基线提交 | `08f2e81`（升级前快照，可回滚到此） |
| 最新提交 | 见 `git log -1` |
| 远端 | `origin` → `git@github.com:WeGreen-AI/Synwevo.git`；开发修改和部署修复已经授权推送。旧记录“目标仓库为空 / 尚未提交”已失效 |
| 服务器 | `vultr_tokyo`，复用现有 Cloudflare Tunnel；切换进展和验收见 deployment.md |



## Synwevo 品牌统一与仓库迁移轮（本轮，2026-09-24）

平台正式定名 **Synwevo**；`origin` 切到 `git@github.com:WeGreen-AI/Synwevo.git`。
完整映射与保留理由见 [`docs/migrations/synwevo-rebrand.md`](../migrations/synwevo-rebrand.md)。

| 对象 | 名称 |
|---|---|
| 平台 | Synwevo |
| 首页 / 工作空间 | Synwevo Workspace |
| 统一账户 | Synwevo Account |
| 文档应用 | Synwevo Docs |
| 仓库根目录 | `/Users/lihiko/repo/wegreen/synwevo` |
| 文档应用模块 | `apps/docs/`（Python 包 `synwevo_docs`） |

### 本轮已验证完成（本机实跑）

| 验证 | 结果 |
|---|---|
| 后端测试 | **665 passed, 1 skipped**（上一轮 650 + 15 条兼容规则新测试） |
| 模块依赖边界 | 159 个文件 / 7 个边界 / 0 处越界 |
| 旧品牌残留审计 `scripts/check_brand.py` | **0 处未分类**；全部归入有名字的保留理由 |
| 前端 typecheck + build | 三个外壳全部通过 |
| 统一登录链路 `verify_sso.py` | **38/38**，且输出里的平台名称为 `Synwevo` |
| 浏览器实跑 | Synwevo Workspace 首页 → 登录 → 应用卡片显示「Synwevo Docs」→ 工作台；账户页、管理后台、375px 窄屏 |
| 机器自动化 | 服务账户取令牌 → 提交 → 10 个真实 .docx，逐条打开核对；无凭据 401 |
| 旧配置兼容 | 只设 `DOCWEAVE_*` 的真实进程正常启动，每个变量提示一次；`platform_name` 报告 Synwevo |
| 配置冲突拒绝 | `SYNWEVO_DATA_DIR` 与 `DOCWEAVE_DATA_DIR` 不一致时**退出并报出两个值**，不猜 |
| 口令黑名单 | 含 `synwevo` 的口令被拒（实跑时真的挡住了一次） |
| Git | 历史完整、工作区无丢失、`origin` fetch/push 均为 Synwevo 仓库 |

### 本轮修复的、由改名引入的问题

| 问题 | 怎么发现的 |
|---|---|
| `env` 模块被同名局部变量遮蔽（5 处） | 导入即 `UnboundLocalError`；用 AST 扫描定位全部 |
| 窄屏下品牌名变长，顶栏折成三行并压住内容 | 375px 浏览器实跑 |
| 两次前缀替换叠加产生 `PLATFORM_SYNWEVO_PORT` | 读改完的代码 |
| 虚拟环境里记着旧绝对路径 | 目录改名后 `import` 直接失败；重建 venv 而不是打补丁 |

### 差一点被改掉的东西

`mfa.py` 里的 `b"docweave/mfa-seed/v1"` 与 `"docweave/recovery/v1:"` 是**密钥派生的域分隔标签**，
不是品牌文案。跟着改会让库里所有已加密的 TOTP 种子解不开、所有已发出的恢复码对不上，
而且不会有任何报错指向改名。现在代码里有注释，审计脚本里有保留理由。

### 本轮未做

* **未推送**：没有 `push`、没有强推、没有远端删除；目标仓库仍是空的。
* **未部署**：未改生产 DNS、未建隧道、未公开暴露；服务器上的容器仍叫 `docweave`。
* 未创建、删除或改名任何 GitHub 仓库，未注册域名。
* 未发布任何 npm 或 Python 包；`@synwevo` 与 `synwevo-*` 都只是仓库内部命名。

## 平台单仓库重组轮（上一轮，2026-09-24）

把仓库变成**平台母仓库**：一个 Git 仓库，内部分开门户、Synwevo Account、Synwevo Docs 与三个共享包，
各自独立构建、启动与测试，根目录提供统一的运行命令。

上一轮的结论是「不搬目录」，理由写在 `docs/architecture/platform.md` §1。本轮搬了，但没有取消
那个顾虑：它被拆成四个分开验证的步骤（目录改名 / 纯移动 / 边界修复 / 运行整合），过程与证据在
[`docs/migrations/platform-restructure.md`](../migrations/platform-restructure.md)。

| 提交 | 内容 |
|---|---|
| `676a6a3` | 先写下迁移映射、Git 保全方式、数据处理与恢复步骤，再动手 |
| `63811c5` | 213 个文件**纯移动**（`git mv`，全部识别为重命名），不改一行导入 |
| `e1ae385` | 真实的包边界：五个可安装发行版、账户服务不依赖 Synwevo Docs、账户界面只剩一份、角色组装层 |

### 本轮已验证完成（本机实跑，非推断）

| 验证 | 结果 |
|---|---|
| 后端测试（三个套件） | **650 passed, 1 skipped**；迁移前基线是 644 passed, 1 skipped |
| 模块级单跑（`apps/docs` 目录内） | 306 passed, 1 skipped |
| 模块依赖边界 `scripts/check_boundaries.py` | 156 个文件 / 7 个边界 / **0 处越界**；无 `sys.path` 改写、无机器绝对路径、无指向旧目录的符号链接 |
| 前端 `npm run typecheck && npm run build` | 三个外壳全部通过 |
| 统一登录链路 `tests/integration/verify_sso.py` | **38/38**（在重组后的三个进程上实跑） |
| 真实数据完整性 | 44 张表逐表行数与迁移前备份**完全一致**；69 个 project id 逐一相同；695 个 blob 摘要清单**逐字节一致** |
| `default` 历史数据空间 | 仍是 `legacy / pending_claim / 0 成员`——本轮没有认领，也没有重置 |
| 真实库迁移演练 `tests/integration/migration_rehearsal.py` | **15/15**，隔离副本、来源只读 |
| 一致性备份与恢复演练 | `backup` → `verify-restore` **20/20**，在隔离副本上真的恢复出来再逐项比对 |
| 数据目录解析 | 从平台根目录、`apps/docs/`、`services/identity/` 三处启动，加载的是同一个库 |
| 空数据目录守卫 | `up` 在没有 `docweave.sqlite3` 时退出码 2 并说明原因，不静默建空库 |
| 干净检出 | `git clone` 到新目录 → `python scripts/platform.py setup` 一条命令装好并构建成功 |
| 浏览器实跑 | 见下 |
| 机器自动化 | 见下 |

### 三条主链路（本轮真实执行）

1. **平台入口**：门户 → 统一登录（账户服务，输一次口令）→ 门户首页看到 Synwevo Docs 卡片 →
   打开工作台（不再要口令）→ 批量生成 10 份 → 下载。产物**打开核对**：每份内容不同，
   客户名称/编号/日期/金额逐条正确，blob 的 SHA-256 与文件名相符。
2. **应用直达**：清空会话后直接打开深链接 `docweave.localhost:8765/p/p_…` → 跳统一登录 →
   **回到该深链接本身**（不是首页）。停掉门户后，账户服务 + Synwevo Docs 照常工作
   （`/healthz` 200 / 200，门户 000）。
3. **机器自动化**：服务账户取令牌 → 建项目 → 上传来源与模板 → 建字段与映射 → 预检 → 提交 →
   事件流 → 下载 10 个真实 `.docx`，全程无浏览器、无 Cookie。负面用例同样实跑：
   无凭据 401、仅 `openid` 的身份令牌调业务接口 401、同一幂等键两次提交得到**同一个 job id**、
   停用服务账户后令牌立即 401（`client_unavailable`）。

### 界面实跑（真实浏览器）

门户首页、登录页、账户与安全（账户信息 / 工作空间 / 登录设备与撤销 / Passkey / 两步验证）、
平台管理后台（概览 / 账户 / 工作空间 / 审计）、Synwevo Docs 工作台与生成结果页，桌面与 375px 窄屏
各走一遍；静态资源、样式与字体正常，本轮没有引入运行错误。管理后台的工作空间视图只返回**数量**
（`projects: 1`），不返回项目名称或内容——平台管理员不因迁移获得业务文档读取权限。

### 只在合成样本上验证

* Passkey / WebAuthn 由软件虚拟认证器驱动（19 条测试）；真实生物特征与实机认证器互操作未验证。
* 邮件走本地投递目录；真实送达、域名与邮件信誉属外部条件。
* 业务数据全部为合成夹具；真实业务文件的结构差异未覆盖。

### 本轮未做

* **未部署到服务器**：未改生产 DNS、未建隧道、未公开暴露、未推送任何提交。
* 未创建、删除或改名远程仓库；当时 `origin` 是 `git@github.com:WeGreen-AI/DocWeave.git`（2026-09-24 的品牌轮才切换）。
* 未做工作流编排产品；新应用与编排类产品的接入方式写在
  [`docs/integration/app-onboarding.md`](../integration/app-onboarding.md) §10。

## 平台身份轮（上一轮）

把 Synwevo Docs 的身份体系提升为多应用平台账户体系：账户服务是 OIDC Provider（Authlib
1.6.5，BSD-3-Clause），Synwevo Workspace和 Synwevo Docs 是两个独立接入的客户端。

| 提交 | 内容 |
|---|---|
| `b0af190` | Provider 数据模型与核心：持久化签名密钥、客户端登记、授权码（PKCE S256、单次使用、重放即撤销）、令牌、id_token 携带 `auth_time`/`amr`/`sid` |
| `e2e9908` | 接上 HTTP 端点；刷新令牌轮换 + 重放检测；`/oidc/logout` 与后通道登出；OpenAPI 组件裁剪 |
| `f7f11dd` | Synwevo Docs 作为依赖方（BFF）；三角色边界；本机三来源运行与量测脚本；应用/客户端/密钥 CLI |
| `10cd2bd` | Synwevo Workspace、账户服务独立外壳、依赖方不再显示口令框、真实库迁移演练脚本 |
| `3598125` | 平台管理后台与历史数据认领；带作用域的重认证（此前只有一个硬编码的 `mfa`） |
| `7cca309` | 机器身份：服务账户（客户端凭据）与用户委托；每次调用重新核验 |
| `1b82b9a` | Passkey / WebAuthn：登记、登录、改名、删除；`amr` 区分持有与已验证本人 |
| *(本轮未提交部分)* | 找回口令与邮箱验证的路由与页面 |

**本轮已验证完成**（本机实跑，非推断）：

* 后端测试 **全绿**（见下方「当前状态」）。
* `scripts/verify_sso.py` **38/38**：三条链路、Cookie 主机隔离（用真实 `http.cookiejar` 量出，
  非按端口假设）、角色边界、跨应用登出、未登记跳转地址被拒。
* **自动化链路本机实跑**：CLI 登记服务账户 → 客户端凭据取令牌 → 建项目、上传来源与模板、
  写映射、预检、提交任务 → 真实状态事件 → 下载 10 个 .docx（每个约 39 KB，含真实客户数据）。
  全程无浏览器、无 Cookie、无点击。
* **真实浏览器走通**三条链路，包括深链接 `docweave.localhost/capabilities` →
  账户服务登录 → **回到 `/capabilities` 本身**。
* `scripts/migration_rehearsal.py` **15/15**：真实 69 项目库的一致快照副本上跑迁移，
  项目/模板/任务/产物逐行指纹不变，`default` 仍 `legacy/pending_claim/0 成员`，
  重复执行幂等，来源只读未被改动。

**本轮发现并修复的真实缺陷**（都不是推断出来的，是跑出来的）：

| 缺陷 | 怎么发现的 |
|---|---|
| 新建工作空间没有启用任何应用——登录成功后被告知什么都不能用 | 本机起服务后用 CLI 建账号，链路走不通 |
| 换码请求被环境里的 `HTTP_PROXY` 静默劫持（`NO_PROXY` 不匹配 `accounts.localhost`） | 浏览器跳转全对，只有服务端换码失败 |
| uvicorn 绑了 `127.0.0.1` 而域名先解析到 `::1`；浏览器会重试，httpx 不会 | 同上，逐层排查到 socket |
| 账户服务渲染 Synwevo Docs 工作台，把一次正确的角色拒绝显示成坏页面 | 浏览器打开 `accounts.localhost` |
| 依赖方显示自己的口令框，而它根本不提供 `/api/auth/login` | 浏览器点门户上的 Synwevo Docs 卡片 |
| 门户/账户服务进程会把 Synwevo Docs 的启动地址覆盖成自己的 | 点卡片落回登录页 |
| 畸形的后通道登出令牌逃逸成 500，让提供方永远重试 | 写否定用例时 Authlib 抛了自己的异常类型 |
| 只有一个硬编码 `mfa` 的重认证作用域：为加 Passkey 证明身份同时解锁了管理操作 | 写认领流程时发现 |
| 刚登录满足所有作用域，于是刚登录的管理员永远看不到确认提示 | 同上 |
| `/api/admin/` 在业务角色上可达（角色过滤器是「列黑名单」而非默认拒绝） | 写角色边界用例时发现 |
| 认领完成后工作空间仍叫「历史数据（待认领）」，切换器每天这么显示 | 浏览器里看见 |
| 门户/账户服务进程覆盖 Synwevo Docs 的启动地址（`app_id()` 默认值是 `docweave`） | 点卡片落回登录页 |
| `/api/auth/forgot` 在邮件失败时 503、未知地址 200——防枚举端点变成枚举工具 | 写枚举用例时发现 |
| `deliver or default`：可调用但为假值的对象被悄悄换成真实网络投递 | 测试替身是个空列表，于是测试在偷偷打真网络 |
| 提交的 OpenAPI 文件带着它并不描述的路由的组件 | 新增表单路由后同步检查失败 |

**仍需用户材料或外部条件**：真实脱敏业务样本；生产域名（影响 Passkey 的 RP ID）；
生产邮件投递（本地接收器不能冒充送达）。

## 本轮已解决

| 批次 | 提交 | 内容 |
|---|---|---|
| 任务可靠性 | `44c9b70` | 原子认领 + 租约、事务化发布、取消裁决、运行记录、事件、持久化幂等（DW-001…009、011） |
| 机器接口 | `a7d0db9` | `/api/v1`（39 路由）、API 密钥 + 租户 + 权限范围、声明式定义、健康检查（DW-010、012、030、031） |
| 文档与调用 | `68778cd` | Webhook、`scripts/automation_example.py`、`docs/integration/*`、OpenAPI、运维文档（DW-032） |
| 界面与操作 | `73d8e84` | 命令面板、映射列表键盘与批量操作、主题、记忆布局、可调面板（DW-043…046） |
| 性能 | `5a0c62b` | 大表分页复用已解析工作簿、去掉重复窗口请求、测试路径修复（DW-040…042） |
| 文档与 E2E | `9ac6764` | `ui_e2e_keyboard.py`、`docs/design.md`、更正过时的能力声明 |
| 安全 | `4b3bbd0` | 监听守卫真正生效（DW-047，**P0**：此前 `SYNWEVO_HOST=0.0.0.0` 可无鉴权对外提供整个界面） |
| 接口契约 | `cd7c6cf` | 鉴权失败的响应也带 `X-Request-ID`（DW-048） |
| 验证记录 | `9fb975a` | `docs/quality/verification.md` |
| 帧时间实测 | `099a926` | `scripts/ui_perf.py`，可见窗口真实帧计时 |
| 许可证 | *(本轮)* | 无 AGPL 部署模式实测并写入 `docs/DEPENDENCIES.md` 第 6 节 |
| 枚举静默回落 | *(本轮)* | DW-049 `layout`、DW-050 字段枚举、**DW-051 合并方式（错填数据）** |

详细证据见 [issues.md](issues.md) 与 [verification.md](verification.md)。

## 当前状态

* 后端测试 **202 passed, 1 skipped**；前端类型检查与生产构建通过；三套浏览器 E2E 通过；无界面调用与 OpenAPI 同步检查通过。
* 用户列出的六项限制已逐项核验，结论见 `verification.md` 第 12 节：四项在代码中早已解决（旧 README 过时），分页有可检测+自动适配的处理，AGPL 有实测可用的无 AGPL 部署模式；只剩「无真实样本」属外部阻碍。
* 范围内已确认缺陷为 0；发布判断见 `verification.md` 第 14 节。

## 第三轮已完成（本轮）

| 项 | 提交 | 证据 |
|---|---|---|
| 唯一跳过项（§7.2） | `04d301f` | 确认是 `test_regression_cases.py::test_committed_cases_still_pass`——`regression/` 为空导致「已提交用例」链路在 CI 中等于没有覆盖。播种一个**明确标注为合成**的用例（每组一份 + 重复明细，15 样本，44 KB）。现在 **203 passed, 0 skipped**。并证明它不是空测试：把期望值改掉后 `run()` 返回 `ok=False` 并给出逐字段 diff |
| 测试污染根因（§6.1） | `0f6b307` | 数据目录在**创建时**记录 `instance_kind` 并由 `/healthz` 暴露；只在目录还为空时写入，因此对已有真实项目的目录设置 `SYNWEVO_TEST_INSTANCE` **永远无法**把它改成测试实例（有测试锁定）。`scripts/_testenv.py` 要么自动起一个隔离实例，要么拒绝任何 `/healthz` 不报 `instance=test` 的服务器（localhost 或 8765 端口都不算证据）。三套 E2E 默认隔离运行并通过；指向正式实例被拒绝 |
| **DW-052（P0 崩溃）** | `0ecda3b` | 无 PyMuPDF 部署下并发读 PDF **让整个进程 SIGBUS 崩溃**（退出码 138，无 traceback）。8 线程 × 200 次读取复现。修复：`pdfium_shim.LOCK` 进程级可重入锁。修复后 `ok=200/200`、退出码 0。回归测试跑在**子进程**里，去掉锁即失败 |
| DW-053 / DW-054 | `1d54e40` | 文档里的 `SYNWEVO_DOCS_LO_PROFILE` 固定路径会让多进程共用 LibreOffice 配置目录（`_LO_LOCK` 跨进程无效）→ 改为父目录语义，始终 `<父目录>/profile-<PID>`；图片模板忽略可配置像素上限且未加载解压炸弹防护 → 改走 `LIMITS` |

## 第三轮调查结论（6 个并行探针，已完成）

* **图片模板脱离 PyMuPDF：可行（yes）**。包装 PDF 对图片模板毫无贡献（实测包装页文字数 0、矢量图形数 0）；10 个函数里只有 4 个真正用到 PyMuPDF，且都只用于容器构造/栅格化/子区域渲染。Pillow 原型与现有链路对比：背景色、字号、断行完全一致，填充框外**逐像素相同**，框内 0.26% 差异且全是抗锯齿。**唯一真正的阻碍是中文字体来源**——目前全产品唯一的 CJK 字体是 `pymupdf.Font('cjk')`（嵌在 PyMuPDF 二进制里），`fonts.py` 只解析字体族名、从不返回文件路径。
* **pdfium 写入 PDF：部分可行（partial）**。真删除与中文写入**已实测跑通**（定位文字对象→移除→重建内容流→用两条独立路径确认旧文字消失；`FPDFText_LoadFont(cid=True)` 嵌入中文 TTF 后中文可渲染可提取）。阻碍：PDFium **不做字体子集化**（单文件 1.7 MB vs 现在 17 KB）、**没有排版引擎**（换行/缩放/对齐/旋转要重写）、**非线程安全**、需要在仓库内自带 CJK 字体。
* **版式（§3）**：`fit` 只按页数验收，不检查字段丢失/裁切/重叠；报告的 `factor` 是请求的步进值而非 clamp 后的实际值；**被测量的字节与被交付的字节不是同一份**；失败时版式报告被丢弃（恰恰是最需要它的时候）。
* **首页（§6.2）**：无搜索/排序/分页/虚拟滚动；卡片不可键盘访问；中文界面里直接显示英文枚举 `completed_with_errors`；失败任务与取消/部分失败同为琥珀色；`per_block` 不在标签表内；列表接口对 40 个项目要 81 次 SQLite 往返。归档需要新列（`deleted` 已被软删除占用）。
> **2026-09-23 更正（实测）**：下面那句「按 6 列做 mark-and-sweep 会删掉快照仍在引用的 blob」，
> 在今天的真实库上**是错的**——只能从 JSON 到达的 blob 有 **0 个**，38 个快照 blob 全都同时能从
> 那 6 个直接列到达。两种候选集今天逐字节相同。
>
> **但这个陷阱是潜伏的，不是不存在的。** 原因是结构性的：快照引用只有在底层行被**硬删除**、
> 或模板版本被清理之后才会变成唯一引用，而代码库里目前**没有任何硬删除路径**（`grep "DELETE FROM"`
> 的 15 处没有一处落在 source_files / templates / template_versions / jobs / job_items / artifacts 上；
> `projects prune --delete` 是软删除，`artifacts.deleted_at` 被读取但从没有被写入）。
>
> 危险恰恰在这里：下一个人去"验证"这个陷阱会量到 0，据此断定它是想象出来的，然后在同一个改动里
> 加上硬删除（这是 GC 项目的常见搭档）——陷阱就在那一刻变成真的。`templates.info` 的
> `$.image.original_blob` 同理：今天是 0 行，只因为库里还没有图片模板，第一个图片模板上传就会
> 创建一个 6 列扫描看不见的引用。
>
> 实测数字（真实库只读副本）：磁盘 695 个 blob / 47.6 MB，被引用 694 个，悬空 0 个；
> 直接列 2157 处引用 / 694 个去重；JSON 内嵌 126 处 / 38 个去重（Python 全表扫描与 SQL
> `json_extract` 两种方法独立得到同一组数字）。

* **测试污染（§6.1）**：真实数据目录是 `<repo>/data`，共 68 个项目；其中 36 个没有任何任务，**无法追溯来源**；另有 3 个测试命名的项目被用户在脚本运行数小时后手工改动过——**不能删**。2157 个 blob 引用指向 694 个去重 blob，回收磁盘需要全局 mark-and-sweep。
  **⚠ 这 2157/694 只数了 6 个直接列**（`source_files.{blob,parsed_blob,working_blob}`、`templates.original_blob`、`template_versions.working_blob`、`artifacts.blob`）。另有两类引用藏在 JSON 里，按列扫描看不见：`jobs.snapshot` 的 `$.template.working_blob` / `$.template.fill_options.image.original_blob` / `$.sources[*].{blob,working_blob,parsed_blob}`（真实库里另有 126 处引用、38 个去重 blob），以及 `templates.info.$.image.original_blob`（每个图片模板都会写）。**按 6 列做 mark-and-sweep 会删掉快照仍在引用的 blob，任务重跑/重试随之失效。**
  另外三点在实现前必须处理：① 248 个 blob 被多个项目共享（63/69 个项目都有共享 blob），所以只能按可达性回收，不能按归属级联删除；② `put_blob` 先写文件、后提交引用行，且内容重复时**不重写文件**，所以 mtime 是首次写入时间、永远不更新——真实库里 131 个 blob 在首次写入 1 小时后才获得新引用，单靠 mtime 宽限期判断陈旧是错的；③ `idempotency.request_hash` 与 `api_keys.secret_hash` 也是 64 位十六进制但不是 blob id，全表正则扫描会误标。安全做法：先列磁盘、再读引用、mtime 宽限、在 `BEGIN IMMEDIATE` 内复核候选集、用改名隔离而非直接删除。

## 剩余事项（按价值排序）

0. **平台侧未完成**：成员管理界面（后端已有，
   界面只做了管理后台侧）；工作空间邀请的界面入口；**真实浏览器 + 真实认证器的 Passkey 互操作
   未验证**（需用户生物特征，不擅自发起）。
0.0 **Webhook 已按本轮标准复核**（见 `docs/security/permissions.md` §3.7）。签名、时间戳绑定、
   不跟随跳转、内网拒绝原本就在；本轮补上两处：**校验过的地址与实际连接的地址现在是同一个**
   （此前解析两次，中间那个窗口让 DNS 重绑定可以绕过全部检查），以及**不再继承环境里的代理**
   （代理会把带签名的事件正文送到无人选择的地方，并且自己做解析从而绕过地址校验）。

0.1 **blob 回收只交付了清点，没有交付删除**。`scripts/blob_audit.py` 是只读的，并且**没有删除
   模式**（有测试 grep 它里面不能出现 `unlink` / `DELETE FROM`）。

   > **这份清点的第一版自己就漏了一个引用源，是对抗性复核抓出来的。** 三个独立的审查者一致指出：
   > `blob_grants.blob` 是 schema 里第七个 blob 列，也是 `authz._REACHABLE` 五条可达性规则的
   > 第五条——而第一版 `DIRECT` 只抄了其中四条。预览产物（views.py:292）和排版 PDF（views.py:319）
   > **只**记录在这张表里，于是服务器正在通过 `/api/preview-files/{blob}` 提供的字节，被清点列进了
   > "无人引用"。复现过：一个刚刚返回 200、39514 字节的预览 blob 出现在 `unreferenced_on_disk` 里。
   >
   > `_REACHABLE` 上方的注释本来就写着"以数据形式保存，使读取检查与将来的清理器不会各自漂移"，
   > 而这个清理器照样漂移了——因为它**抄**了那份清单而不是**引用**它。
   >
   > 今天真实库里 `blob_grants` 是 0 行（这张表比任何一次预览都新），所以按行数去"验证"会量到 0
   > 并断定它不存在——**和上面那条 JSON 陷阱是同一种测量错误，只是换了一张表**。区别在于：JSON
   > 陷阱需要将来加上硬删除才成真，这一条**不需要任何代码改动**，只需要有人点一次预览。
   >
   > 修法不是再加一列，而是结构性的：`_cross_check_against_authz()` 会比对 `_REACHABLE` 的每一条
   > 规则，只要清点比授权层更窄就报警。将来给 `_REACHABLE` 加第六条规则，清点会失败而不是悄悄少算。

   **给将来写删除路径的人的两条实测提醒**（来自对删除方案的对抗性复核，方案本身没有实现）：

   - **`put_blob` 是内容寻址的，文件已存在时它什么都不写**。真实库上 2157 次引用创建里有
     **1463 次（67.8%）**走的是这条不写盘的路径。也就是说：一个当前无人引用的旧 blob 文件，会在
     某个生产者算出同样哈希的那一瞬间变成"活的"，而这个主张在它的行提交之前**只存在于那个线程的
     内存里**——磁盘上没有临时文件、库里没有行、没有锁。任何"先列磁盘、再读引用"的扫描都盖不住
     这个窗口。这是不实现删除的第二个理由。
   - **不要用"只复制主库文件"的方式去读引用**。见 identity-migration.md §5.1：这个库有 28 张表
     只存在于 WAL 中，`blob_grants` 正是其中之一。WAL-盲的读取会认为这张表根本不存在，于是所有
     预览产物都变成孤儿。`blob_audit.py` 用普通只读连接（会读 WAL），是对的。这是有意的：见上面那条更正——
   六列扫描今天能通过任何人写的测试，而陷阱要到加上硬删除时才现身。删除路径应当基于这份清点来
   写，并带上隔离（改名而非删除）与 `BEGIN IMMEDIATE` 内的候选集复核。
0.2 **编辑器图片仍然内联**。已加上 24 MB 的请求体上限（可恢复的 413）和插入前的降采样，
   根因（图片走文档模型提交，而不是走独立的上传接口）未改。彻底的做法是新增资源上传路由 +
   blob 读取路由，把 `src` 换成引用——那是一次更大的改动。
0.3 **长时间同步请求：实测结果，以及一处已修**。
   先更正前提：会话里一直转述的"原记录中的六处长时间同步请求"在 `docs/quality/` 里**并不存在**。
   这里唯一的"六项"是 verification.md §12 的六项已知限制（公式、分页、重复明细、图片模板、
   AGPL、合成数据），与超时无关。因此本轮从零实测（单客户端、单进程、Apple Silicon、隔离数据
   目录、LibreOffice 与 tesseract 均在位）：

   | 处理器 | 实测最坏情况 | 状态 |
   |---|---|---|
   | `POST /api/projects/{pid}/jobs` | **74.4 s @ 2 万条**（= `SYNWEVO_DOCS_MAX_RECORDS_PER_JOB` 默认值） | **未修**。本该只是"接收任务"的请求，在返回 id 之前为每条记录建计划、渲染文件名、插入 job_item（jobs.py:526）。`/api/v1` 那条声明了 `202`，但这个 202 迟到 74 秒；UI 那条干脆返回 200。期间没有进度事件，也无法取消 |
   | 预检 `generation-summary` / `/api/v1/…/preflight` | 5.3 ms @100，9.2 s @5000，**74.4 s @2 万**；线性约 3.7 ms/条，**无缓存**（同样参数第二次仍要 8.0 s） | **未修**。同一个原因；纯 Python 受 GIL 限制，4 个并发调用者基本串行 |
   | UI 的 ZIP 下载 `/api/jobs/{jid}/zip` | RSS **+222 MB**（需要约 13 MB） | **已修**：改为从磁盘流式传输，并删掉 `JobService.zip()`。`/api/v1` 一直是对的，只有 UI 这条在缓冲。3 GB 上限的容器上这是一次 OOM 而不是一个错误页 |
   | PDF 全文搜索 | 每一页都重新打开一次 PDF | 未修 |
   | OCR 整份文档、脱敏、预览排版、模板上传 | 2.3–3.9 s / 0.6–1.7 s 等，均在常见代理超时内 | 暂不改；`LIMITS.converter_timeout_s = 180 s` 长于常见的 60 s 代理读超时，值得单独对齐 |

   前两项是真正的阻断项，但要改的是任务创建的结构（把计划构建移进 runner，让请求只落一行
   `queued`），属于一次独立改动，不适合与本轮身份工作混在同一个提交里。按实测数字如实记录，
   **不写成"已修复"**。

1. **真实业务样本**：仍为外部阻碍（未取得脱敏真实文件）。拿到后用界面「保存为回归用例」补充回归集。
2. **Chrome 以外的浏览器**：未测试，不声称兼容。
3. **多进程共享数据目录的长时间并发**：只验证过同进程并发与单次中断恢复。
4. **小屏编辑能力**：当前定位为「查看 + 任务下载」，未做移动端编辑，也未在文档中声称。
5. 少量无调用点的历史辅助函数（`anon_id`、`docx_check`、`preview_removed_text`、`clear_cell_text`、`refers_to_sheet`、`fmt_path`、`norm_text`、`text_color`、`remove_fields`）。已确认没有文档声称它们的行为（与 DW-047 不同），属于可清理项，优先级最低。

## 下一条实际行动

本轮交付条件已满足，「枚举静默回落」缺陷类已排查完毕（`combine`/`aggregate`/`transforms`/`layout`/字段枚举/样本模式/`error_policy`/`formats` 全部确认）。若继续：在 60 Hz 或更低配置的机器上重跑 `scripts/ui_perf.py`，或在拿到脱敏真实文件后用界面「保存为回归用例」补充回归集。
