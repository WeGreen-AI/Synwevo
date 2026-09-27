# 当前产品轮交付记录

## 2026-09-25 统一白蓝设计系统与多种登录方式（本轮，已部署）

**目标**：平台、账户、Docs、平台后台与 API 台形成一套白蓝浅色体系；补齐「保持登录」、邮箱验证码登录与
外部身份提供商（Google / Microsoft 等 OpenID）登录、关联与解绑。执行代理为 Claude Code。
本节先于下方历史记录；部署状态以 [deployment.md](deployment.md) 为准。

### 已实现（本机实跑）

| 范围 | 实际行为与证据 |
|---|---|
| 设计令牌 | `packages/ui/src/styles.css` 收敛为白蓝基线（`--bg #F8FAFC`、`--accent #2563EB`、hover `#1D4ED8`、pressed `#1E40AF`、`--accent-weak #EFF6FF`、`--text #0F172A`、`--text-2 #475569`、`--line #E2E8F0`），控件边界 `--line-strong #8894A8`（3.1:1），对比度写在令牌旁；深色主题退役，`data-theme`/系统深色不再改变外壳（E2E G 流程验证）。 |
| 门户 | `portal.css` 从独立绿色主题迁移到共享令牌与共享 `.btn`；公开首页、登录后门户、状态页；品牌标记统一为 `Brand`。桌面 1024 与 375 窄屏截图检查通过。 |
| 账户 / Docs 外壳 | 顶栏、登录框架、Docs 三种页面共用 `Brand`/`BrandMark`；API 台去掉兜底色；Toast 改为白卡+状态条；内联字面色改令牌或 `color-mix()`；三个 `index.html` 统一 `lang`、`theme-color`、`color-scheme`。 |
| 守卫 | 新增 `scripts/check_design.py`（CI 与验证清单已接入）：共享令牌外的字面颜色必须归入有理由的例外；禁止深色主题、私有色板、自选字体、`@media` 外 `!important`。当前通过。 |
| 保持登录 | Store v7 `sessions.persistent`；勾选后中央会话空闲 14 天（`SYNWEVO_SESSION_PERSISTENT_IDLE_S`）、绝对 30 天不变、两个 Cookie 带 `Max-Age`；平台管理员忽略；只接受 JSON `true`；设备列表标记。契约见 sessions.md §2.1。`test_remember_me.py` 7 项。 |
| 邮箱验证码登录 | `/api/auth/login/email/start|verify`：6 位码、10 分钟、单次、5 次错误作废、同形响应防枚举、发出后 outbox 脱敏；启用 TOTP 仍走第二步（`amr=email+totp`）；管理员未启用 TOTP 不发码。三语言邮件模板。`test_email_code_login.py` 9 项。 |
| 外部身份提供商 | `synwevo_identity/external.py`：发现文档、PKCE S256、服务端 state/nonce、id_token 全量校验（签名/iss/aud/azp/nonce/exp/iat/sub，Microsoft 多租户模板）、按 `(provider, subject)` 关联、已验证邮箱匹配只生成关联要约、注册策略生效、管理员需 TOTP、关联/解绑需 strict 重认证且不能移除最后一种登录方式、安全通知。配置不完整拒绝启动；未配置不显示。`test_external_identity.py` 16 项（测试替身提供商，含篡改用例）。 |
| 账户内改口令 | `POST /api/auth/password`：当前口令 + 新口令；本会话保留、其他会话与令牌失效、安全通知、审计；账户页「邮箱与口令」内置表单，邮箱重置保留为备用。`test_password_change.py` 8 项。 |
| 登录页 / 账户页 | 登录：保持登录复选框、「其他登录方式」（安全密钥 / 邮箱验证码 / 已配置提供商）、外部登录错误解释、关联要约表单、片段续接 MFA；账户：外部登录方式卡片（关联/解绑）、设备列表登录方式与「保持登录」标记。三语言资源补齐，账户外壳 19 项校验通过。 |
| 文档 | `docs/design.md` 重写为平台设计系统；`sessions.md` §2.1/§4.3–4.5、`permissions.md` 契约表、`.env.example`、README/TESTING/AGENTS/CLAUDE 验证命令。 |

### 本轮运行证据（本机，2026-09-25）

| 运行范围 | 结果 |
|---|---|
| `services/identity/tests` + `tests/integration` | **1104 passed / 0 failed**（296s；含本轮新增 40 项与 v7 迁移测试） |
| `apps/docs/tests` | 全部通过：840 项收集、0 失败、1 项既有 LICENSE 跳过（退出码 0） |
| 前端 | typecheck/build 三外壳通过；UI 11、auth-client 31、Docs 45、账户外壳 19 项通过 |
| 守卫 | `check_boundaries` / `check_brand` / `check_design` 通过 |
| Docs 浏览器 E2E | `ui_e2e.py`（主链路：上传→模板→字段→映射→样本→预览→生成 100 份→下载核对）与 `ui_e2e_keyboard.py`（命令面板、列表键盘、单一主题与纸面、布局记忆）在各自隔离实例上通过。`ui_e2e.py` 中一处早已过时的对话框文案断言按当前界面更正。 |
| 隔离实例浏览器实跑（317xx，`instance=test`，file 邮件，本机 OpenID 替身 `idp_double.py`） | 引导管理员→验证邮件；公开注册 Bob→邮件激活；勾选保持登录登录（会话 `persistent=1`、空闲 336h）；账户页关联 Test IdP→替身同意页→回到账户页显示关联；登出后「以 Test IdP 登入」直接登录（`amr=idp:testidp`）；登出后邮箱验证码登录（outbox 发出后不再含验证码）；门户与 Docs 经统一登录静默进入。 |

### 边界与未完成

- 外部提供商只用**测试替身**验证了 Synwevo 一侧的协议与关联规则；Google / Microsoft 的实际应用注册、回调与联调需要运营方在提供商处登记并填入凭据后才能完成，生产未配置任何提供商（登录页不显示按钮）。
- 邮箱验证码在生产依赖已配置的 SMTP；本机只证明 file 投递。
- 仍未做：本轮未重做 Docs 引擎、API 控制台、组织/空间流程的功能性变更；它们沿用上一轮的实现与验证，本轮只改外壳样式并跑通既有回归与 E2E。
- 部署：`4ecdfb0` 与 `dbd4c94` 已依次经 CI 验证并部署（备份 `20260925T042037Z`、`20260925T044056Z`），线上匿名冒烟见 [deployment.md](deployment.md)；线上未做登录态流程验收。
- 本机 317xx 隔离实例（`instance=test`，数据在会话 scratchpad）与 31780 端口的 OpenID 替身仍在运行供查看；停止：在同一环境变量下 `python scripts/platform.py down`，替身进程直接结束即可。

---

**部署接续（2026-09-25）**：用户已在本机交付之后明确授权部署，主页已更正为工作流程自动化平台，
Docs 单独列为应用。`main` 源码 `1a48359` 已通过完整 CI 并部署；1893 项运行环境测试通过、2 项明确跳过。
线上注册已开放，原有账户/文档/密钥和 57 个 blob 核验保留。发布、浏览器与 SMTP 验证见 [deployment.md](deployment.md)。
下面的“未部署”描述是本机交付 `4caea87` 当时的边界，不是当前部署进展。

更新于 2026-09-25。范围：账户自助、组织/工作空间、管理员 API 台、公共门户及简体中文、繁體中文、English。
从干净 `main` 的 `a20537bb642e0f3a54d4241a371000fcd09e8515` 继续；本文件与实现一并形成本地交付提交，最终提交以 `git log -1` 为准。
**未推送、未部署、未访问 VPS、未改 DNS、未发送外部测试邮件。** `main` push 会触发生产工作流，因此本轮不推送。
上轮生产及 SMTP 记录仍见 [deployment.md](deployment.md)，不计入本轮验证。

## 持续迭代记录（2026-09-25 起）

每个批次一行：ID、依据、状态、下一步。研究结论与前后证据在 [verification.md](verification.md) §16.5 起，缺陷表在 [issues.md](issues.md)「持续迭代」。

| ID | 批次 | 状态 | 下一步 |
|---|---|---|---|
| CI-01 | 手机回流与触控目标（WCAG 2.2 1.4.10 / 2.5.8 / 2.5.5，Fluent 2 断点） | 已实现、三宽度审计 0 发现；`9b10602` 已推送，部署见 deployment.md | 把 `ui_mobile_audit.py` 纳入每次外壳改动后的回归 |
| CI-02 | HTTP 安全头（ASVS V14.4：nosniff、强制 CSP、HSTS、COOP/CORP、Permissions-Policy） | 已实现、6 项契约测试 + 五套浏览器脚本 0 CSP 违规，待部署 | 部署后 `curl -I` 线上三源核对；若线上控制台出现 CSP 拦截，用 `SYNWEVO_CSP` 临时放宽并修正策略 |
| CI-03 | 明细表映射对话框语义（每组一份显示错误的「随文件变化」） | 已修复，`ui_e2e_more.py` 恢复运行并通过，待部署 | 五套 Docs/外壳浏览器脚本都进入每次发布前的回归 |
| CI-04 | 内容态手机补审（组织页链接、Docs 首页表格、选择栏提示） | 已修复，两组账户 × 三宽度 0 发现，待部署 | 隔离实例已有组织/邀请夹具，后续审计直接复用 |
| CI-05 | 共享菜单/抽屉键盘模式回归；WebKit 焦点陷阱与开启者回焦两处修复 | 已修复，双引擎通过，待部署 | 后续所有对话框都经 `useModalFocus`，无需逐个处理 |
| CI-06 | Cloudflare 信标与 CSP：仅 `CF-Ray` 请求放行 Web Analytics 两个源 | 已实现，7 项契约测试通过，`59aa880` 已推送 | 部署后用匿名 Chromium 复查线上 CSP 违规应为 0 |
| CI-07 | 对比度采样加入 `ui_mobile_audit.py`（SC 1.4.3） | 采样器已验证；两组账户 0 发现，无需改动 | 每次改令牌或新增文本样式后重跑 |
| CI-08 | WebKit 经本地代理覆盖三个源；五套 Docs/外壳脚本都支持 `E2E_ENGINE=webkit` | 已实现，WebKit 与 Chromium 同等覆盖（Safari Tab 策略差异已在断言中注明） | 每次发布前两引擎都跑 |
| CI-09 | 手机提示条遮挡对话框主按钮（WebKit 发现） | 已修复，待部署 | — |
| CI-10 | 账户外壳按路由拆分（登录页 JS −46%） | 已实现，待部署 | 部署后用匿名 Chromium 核对 `/login` 只加载入口块与共享块 |
| CI-11 | Docs 外壳按路由拆分（项目列表 JS −32%），`lazyRoute` 共享化 | 已实现，待部署 | 共享块 `perm` 274 KB 可再拆（列表页与工作台共用的视图层） |
| CI-12 | 英文残留：设备句子与个人空间默认名改为按语言 | 已修复，待部署 | 审计 `--locale=en` 纳入回归 |
| CI-13 | 顶栏溢出与空间切换器塌缩（共享 `.topbar` 换行） | 已修复，待部署 | — |
| CI-14 | SDK 对网关 `rate_limited` 的有界重试；SDK/CLI 与 Node 验收复跑 | 已修复并复跑通过 | SDK 源码改动后 `pip install apps/docs/sdk/python` 重装再跑验收 |
| CI-15 | API 台浏览器验收在强制 CSP 下复跑（脚本改用 instance=test 规则与函数形式页面脚本） | 通过 | 每个里程碑复跑；夹具在 scratchpad（0600，合成账户） |
| CI-16 | 主按钮悬停对比度（全站 `<button class="btn primary">`）；API 台六页签内容态审计 0 发现 | 已修复，待部署 | 审计已覆盖 API 台页签，移出候选队列 |
| CI-17 | 有映射的工作台与英文长标签的三处手机问题 | 已修复，待部署 | — |
| CI-18 | 繁體残留检测（0 处）；整页目标测量；折叠头与页脚链接目标尺寸 | 已修复，待部署 | 审计三种语言（默认 / `--locale=en` / `--locale=zh-Hant`）都纳入回归 |
| CI-19 | 平台闭环浏览器回归 `services/identity/scripts/ui_e2e_onboarding.py`（注册→验证→登录→引导→邀请→接受→退出，Chromium + WebKit，手机视口） | 通过 | 每个里程碑复跑；需要一个 24 小时内邀请数未达 50 的团队空间（脚本参数），注册限 10 次 / IP / 15 分钟 |
| CI-20 | 恶意上传防护：隔离优先的接纳链（clamd 扫描、`rejected`/`scan_failed` 状态、下载门、模板同步扫描、schema v8、readyz 组件、SDK 等待、三语言标签、compose 参考服务） | 已实现（本机），待部署 | 生产：运维确认 VPS 内存余量 ≥2 GiB，合并 `clamav` 服务与两个环境变量到 `/opt/synwevo/config/compose.yaml`；`rescan` CLI 与加密文件流程未做（file-acceptance §9） |
| CI-21 | 风险触发的人机验证（Turnstile 服务端校验：四个匿名邮件入口每次、口令登录同地址 3 次失败后；`challenge_required/failed/unavailable`；故障不放行；账户外壳组件三语言） | 已实现（本机），待推送 | 生产：账户持有人在 Cloudflare 创建 Widget（`accounts.wegreen.ltd`）并把两把密钥写入 `runtime.env`；测试密钥的令牌不带 action，浏览器完整通路要等真实 Widget |
| CI-22 | 平台通知中心（核心表 + 每角色接口 + 邮件 outbox 调度器 + 铃铛与偏好页；安全/邀请/角色/移出/退出事件接入） | 已部署（a02baf0） | Docs 侧发射点（文件被拒、任务完成）随下一批；Web Push 未做；`docs/platform/notifications.md` 是权威记录 |
| CI-23 | 组织自定义角色（权限目录 + 角色表 + 委派不越权 + 有效权限视图 + 组织页 UI） | 已部署（07af1f0） | `org.members.import`/`org.approvals.decide`/`org.messaging.manage` 由 CI-24/26/25 消费；组织审计页未做；`docs/platform/organization-roles.md` 是权威记录 |
| CI-24 | 机构邮箱注册（CUHK）：根因定位到收件网关（Trend Micro）之后；校验器不再 500、正文 QP、收件页发件人与网关提示、活跃账户再注册寄提示 | 已部署（544f607） | 真实 CUHK 收件、SMTP2GO Activity 记录、SPF/发件域验证/跟踪关闭/From 改域均为账户持有人或运营步骤（operations.md §6.1）；验证脚本 `ui_e2e_institutional_mail.py` |
| CI-25 | 多格式成员批量导入（核心模块 + Docs 读取器注入 + 账户路由/执行器 + 组织页向导 + 接受时分配角色） | 已部署（33f17c6） | 生产 accounts 容器合并 compose 的扫描器两项；`docs/platform/member-import.md` 是权威记录；脚本 `ui_e2e_member_import.py` |
| CI-26 | 组织内私聊与群聊（核心模块 + REST/SSE + 页面 + 平台栏徽标 + 通知事件 + 附件接纳链 + 撤权关门） | 已部署（085667e + 构建修复 7d4d567） | Docs 链接卡片、Web Push、多 worker 事件总线未做；`docs/platform/messaging.md` 是权威记录；脚本 `ui_e2e_messaging.py` |
| CI-27 | 个人 Docs 项目经目标审批复制到组织工作空间（核心状态机 + Docs 清单/执行器/路由 + 账户审批路由 + 组织页审批区 + Docs 对话框 + 通知事件） | 已部署（bcf8729） | 反向/组织间复制、审批人裁剪清单不做；`docs/platform/project-copy.md` 是权威记录；测试 `tests/integration/test_project_copy.py`；脚本 `ui_e2e_project_copy.py` |

已核对、无需改动（记录以免重复研究）：

* **值保真**：`test_engine.py::test_value_fidelity_xlsx` / `test_number_formats` 已覆盖前导零、18 位长编号、0 与空、布尔、货币显示与精确值、日期序列与 1904 体系；`Value` 模型分开 exact/display。未发现缺口。
* **不可信/超大输入**：`test_engine_untrusted.py`（27 项：zip 声明总量与伪造成员大小、超大 XML 部件、行号空洞放大、像素预算、
  GIF 逐帧、正则超时不阻塞其他线程、LibreOffice 无服务端秘密/宏与链接禁用/网络公式不重算、Tesseract 语言列表一次）、
  `test_api_contract_uploads.py`（分片续传、校验和、大小上限、过期、配额）、`test_large_sheet.py` 已覆盖 §六「文件识别与解析」的
  资源上限与损坏输入要求。未发现缺口。
* **批量生成性能**：2026-09-25 复测无退化（verification.md §16.8）。
* **API 幂等**（对照 Stripe「Idempotent requests」，访问 2026-09-25）：Stripe——所有 POST 接受键、键 ≤255 字符、保存首个响应含 500、24 小时后清理、参数不同即报错、并发冲突不保存。Synwevo（api.md §3「幂等」）——按主体+租户+作用域+键、24 小时 TTL 可配、内容不同 `409 idempotency_conflict`、并发由唯一约束裁决、不确定结果 `409 idempotency_outcome_unknown` 且不清理、不支持的操作显式 `400 idempotency_not_supported`。语义等价或更严格；差异只在 Stripe 保存 5xx 结果而 Synwevo 让失败的创建可重试，这是有意选择。无需改动。

| 审计 | CI-33…36 新增界面的手机/平板回归审计（320/375/768 px，45 页无回流/目标尺寸/对比度/未翻译发现） | 已完成，verification.md §16.38 | 评论抽屉与模板对话框页签由各自的手机证明覆盖 |
| CI-47 | 表格型模板协同（在场与跳转、单元格级三方合并与冲突选择、他人保存即时载入） | 已部署（随 CI-48：cb2090e，run 36249289199） | 表格逐键实时同步未做（保存即合并）；手机多条提示叠放遮顶栏（共享提示组件） |
| CI-48 | 发布链路修复：进程退出时停止全部后台 worker 并写出实时文档（`synwevo_core.workers` + lifespan 钩子 + conftest 兜底），CI 容器 `--init`，自托管 compose `init: true`；根因与容器复现见 verification.md §16.49 | 已部署（cb2090e，run 36249289199，backup 20260926T150028Z）；CI verify 容器步骤通过、deploy 成功、三源 readyz 就绪 | 长切片中的 job 线程在 `stop_all(wait=3)` 后可能仍在，靠租约过期接管（与崩溃路径一致） |
| CI-49 | 版本操作与实时修改：恢复/撤销/重做在房间有未保存实时修改时 409 `live_edits` 并点名，`discard_live` 显式确认；项目页版本对话框先说明再确认；快捷撤销从不丢弃；手机提示栈限两条 | 已部署（ba539af，run 36250980928） | 表格型模板房间不可写、不受影响；房间状态读取失败时记日志放行 |
| CI-50 | 可追溯：活动记录按成员/对象筛选、上次查看后的更改、行跳转；评论经项目房间实时到达并进活动记录 | 已部署（b46a403，run 36252593410） | 阅读位置只在本机浏览器（按账户+项目），换设备重新开始 |
| CI-51 | 服务重启不丢正在编辑的内容：退出时写出、新进程播种、客户端重连带离线输入汇合 | 已部署（b46a403，run 36252593410） | 多实例仍需粘性路由；SIGKILL 下最后 1 秒依赖客户端重连补上 |
| CI-52 | 命名检查点：保存时可填版本说明（浏览器、机器 API、SDK `note=`、CLI `--note`），历史显示 | 已部署（b46a403，run 36252593410） | 不改已有版本的说明；无单独命名对话框 |
| CI-53 | 变更记录显示块内插入/删除（`before` 列，schema v22；前端 LCS 差异渲染） | 已部署（11202c3，run 36253921338） | 恢复/替换行无 before |
| CI-54 | 版本对话框列出尚未保存为版本的实时修改（共用变更记录渲染） | 已部署（11202c3，run 36253921338） | — |
| CI-55 | 有界更新包（2 MiB，超限只关该连接）、损坏草稿恢复（改用 head 版本并提示）、远端可见延迟测量（回环 1–8 ms） | 已部署（1d15665，run 36255489490） | 公网延迟未测（需两台机器/受控网络） |
| CI-56 | 项目设置变更显示前后值（样本规则/输出/名称/模板/固定版本） | 已部署（1d15665，run 36255489490） | 模板更换只显示「另一个模板」 |
| CI-57 | 里程碑回归：Docs 主链路桌面/手机证明重新通过；修复无账户模式下模板选择器的 401→登录页跳转（403 `accounts_disabled` + 空列表） | 已部署（e51ceb0，run 36304552064） | 生产（session 模式）不受影响 |
| CI-58 | 登录后的落点：无明确目标的登录与账户服务根路径经门户 `/auth/start` 落到平台工作空间，不再停在账户设置页（用户反馈 2026-09-27；platform.md §8） | 已部署（ad2eeeb，run 36313988930，backup 20260927T111919Z）；线上只读核对：新外壳块已上线、`portal_url` 已发布 | 账户持有人下次在 `accounts.wegreen.ltd/login` 直接登录即是线上行为核对（应落到 `synwevo.wegreen.ltd/workspace`）；`/login?next=/account` 仍到账户页 |
| CI-59 | 退出登录的落点竞态：`RequireSession` 不再把退出中的空会话重定向到登录页，两引擎都落在 `/logged-out`（候选队列里的「登出落点差异」由此关闭） | 已部署（8184dc2，run 36315903038，backup 20260927T115350Z）；单元测试 +2，`ui_e2e_onboarding.py` Chromium 8/8 与 WebKit 8/8 都落在 `/logged-out` | 部署后随 CI-58 的线上行为核对一起看：退出后应到「已退出」页 |
| CI-60 | 依赖方的「退出登录」结束平台会话：Docs/门户退出走账户服务登出并回到本源 `/signed-out` 页，不再只撤本应用会话后落到一句不实的「统一登录已退出」 | 已部署（33c3e1f，run 36318459107，backup 20260927T123955Z）；单元 +1、集成 +1，`ui_e2e_shell.py` Chromium/WebKit 桌面段通过；线上只读核对两源 `/signed-out` 200、Docs 块携带新页 | 门户内退出按钮见 CI-61；部署后随线上退出核对 |
| CI-61 | 门户页头加「退出登录」（桌面文字按钮 / 手机账户菜单项），走 CI-60 的平台级退出 | 已部署（e900774，run 36320325701，backup 20260927T131414Z）；`ui_e2e_shell.py` Chromium/WebKit 手机段通过 | 线上行为核对随账户持有人下次在门户退出 |
| CI-62 | Docs「← Synwevo」与账户外壳品牌链接回到工作空间（门户 `/auth/start` 交接），不再回公开首页 | 已部署（41e6f4f，run 36323605133，backup 20260927T140828Z）；`ui_e2e_shell.py` Chromium/WebKit 桌面段通过 | 线上行为核对随账户持有人下次点击 |
| CI-63 | 门户 `/workspace` 页头加「消息」入口（共享 `MessagesLink`，带未读徽标） | 已部署（0c43018，run 36328222361） | 消息独立成应用（message.* 域名）后入口目标改为注册表地址（第九轮） |
| CI-64 | 消息未读徽标在门户/Docs 源真实生效（`unread-count` 移入共享会话路由） | 已部署（d2dde99，run 36330242865） | — |
| CI-65 | Accounts 导航：顶栏常驻「返回工作空间」与来源应用链接、子页面真实父级链接、组织页改用账户外壳 | 已部署（d2dde99，run 36330242865） | 域名切换后目标随 `PORTAL_URL` 变为 synwevo.com/workspace |
| CI-67 | 域名迁移准备：compose/nginx 参考、旧主机 308、`workplace.synwevo.com` 别名、CI 健康检查变量、运行手册 | 线上已切换（2026-09-27 16:39 UTC；三新主机 readyz、旧主机 308、issuer/state/registry 均为新主机；`SYNWEVO_PUBLIC_HOSTS` 已设） | 受控账户核对登录/Docs/账户返回/Passkey 重登记；`message.wegreen.ltd` 加入隧道；旧主机 308 保留 ≥ 90 天 |
| CI-68 | 私信邮件：缺省关、只能未读摘要、寄出前复核、限速、正文默认不带原文、v23 偏好重置 | 已实现，identity/integration 套件通过，待部署 | 消息独立应用上线后同一规则不变；Messages 页读会话时同步把收件箱条目标为已读（现在只靠寄出前复核） |
| CI-69 | Docs 外壳文案精简与术语统一（`docs/platform/docs-copy.md`） | 已实现（worktree 三套浏览器证明通过），待部署 | 账户/门户/消息外壳按同一规则表继续 |
| CI-70 | 域名切换后 Passkey 登录失败的解释与重新登记路径（`passkeys_elsewhere`） | 已实现，待部署 | 切换后受控账户实测：旧 Passkey 登录 → 解释文案 → 口令登录 → 重新登记 |
| CI-71 | 公开页面重写：首页品牌表达、`/privacy`、`/service`、门户文案与术语（`docs/platform/public-pages.md`） | 已实现，待部署 | 持有人决定项（法律主体、联系渠道、管辖、条款版本、费用/SLA、是否点名第三方处理方、分析脚本）见记录 §5；Message 独立应用上线后首页应用卡的入口与说明随之更新 |
| 矩阵 | 9.16 验收/迁移/上线矩阵：每行的已验证行为、证据与未覆盖条件（真机、真实邮箱客户端、生产 nginx Upgrade、多实例） | 已写入 docs/platform/docs-collaboration.md §9 | 已随 11202c3 发布 |
| 回归 | 个人项目复制不携带私人协同房间/未批准历史（9.16）：目标无活动记录、无实时草稿与变更记录，副本只有来源 head 版本 | 已验证，tests/integration/test_project_copy.py（verification.md §16.54） | — |
| CI-46 | Docs 项目页协同（在场与定位、按映射三方合并、他人变更即时提示、归属活动记录） | 已部署（e6197c1，run 36219721565）；生产 nginx 副本待运维加 Upgrade 头 | SheetEditor 协同与活动筛选未做 |
| CI-45 | Docs 实时多人协同（文档型模板：共同编辑、命名光标、在线名单、逐块归属、草稿持久化、只读/回退） | 已部署（c2d78d0，run 36218166471）；生产 nginx 副本待运维加 Upgrade 头 | 生产 nginx 副本要加 `/api/collab/` 的 Upgrade 头（运维）；表格型模板与项目页协同未做；多进程需粘性路由 |
| CI-44 | 系统邮件设计系统（一个布局、文本+HTML、三语言、Logo、通知邮件） | 已部署（e04ff01，run 36214183403） | 真实邮箱客户端渲染需授权收件箱；邮件 Logo 纳入 brand_assets 清单 |
| CI-43 | 正式 Logo 上线（并行会话的品牌接入 + 复核 + Docs 手机品牌位） | 已部署（30a5cfd，run 36212722189） | 邮件 Logo 随 9.15 邮件设计系统交付 |
| CI-42 | 机器 API/SDK/CLI 的模板用量与版本恢复 | 已部署（30a5cfd，run 36212722189） | — |
| CI-41 | 模板版本历史与恢复（恢复即新版本、影响说明、两步确认） | 已部署（30a5cfd，run 36212722189） | 版本差异对比与 v1 机器契约未做 |
| CI-40 | 评论抽屉静默刷新（30 s + 可见性）；深链指向已删除对象时提示 | 已部署（efd3259，run 36211498761） | 评论的服务端推送（SSE）仍未做，轮询为过渡 |
| CI-39 | 预检问题带修复路径（生成对话框与任务页里的「定位到字段/映射/文件」一步到达对象；项目页 `?locate=` 深链） | 已部署（efd3259，run 36211498761） | 对象已删除时的提示见 CI-40 |
| CI-38 | SDK 验收的委托令牌分支（真实登录 + 授权码 PKCE；共享模板 6 个操作在真实服务上有观察） | 已部署（efd3259，run 36211498761） | CLI 对来源文件操作的命令未做 |
| CI-37 | 取值语义端到端核实（XLSX 显示值/前导零/长编号/大整数/空行；CSV 全文本；XLSX 目标六种单元格）；修复 XLSX 目标产物核对按来源显示文本导致百分比/日期样本误判失败 | 已部署（d30d9f4，run 36210142096） | DOCX 表格/页眉未并入本套件（既有验收覆盖） |
| CI-36 | 机器 API/SDK/CLI 的组织共享模板（委托令牌；幂等发布与使用）与覆盖清单重审计 | 已部署（0f1c810，run 36208724682） | 验收流程的委托分支未做；`docs/integration/api.md` §3 与 `apps/docs/sdk/python/operation-coverage.json` 是权威记录 |
| CI-35 | 组织共享模板（发布冻结 head 版本、组织成员在任一空间复制使用、显式更新发布、弃用/恢复、维护者 = 源空间编辑者或组织管理员） | 已部署（81f113f，run 36207199003） | 审核队列/分类/通知/复制前预览未做；`docs/platform/org-templates.md` 是权威记录；脚本 `ui_e2e_org_templates.py` |
| CI-34 | 群公告（管理者写、钉在顶部、成员汇总通知）与举报处理（只快照被举报消息、`org.messaging.manage` 在组织页删除/驳回、重认证、举报人得到结果） | 已部署（48996f3，run 36205658531） | 平台级举报视图与举报人匿名化未做；`docs/platform/messaging.md` §4.2 是权威记录；脚本 `ui_e2e_moderation.py` |
| CI-33 | 项目评论与提及（一级+回复、四种锚点读取时判断、提及只到空间成员、参与者通知、解决/重开、深链高亮） | 已部署（89c706b，run 36203384969） | 行内文字批注与实时推送未做；`docs/platform/docs-comments.md` 是权威记录；脚本 `ui_e2e_comments.py` |
| CI-32 | 浏览器推送：VAPID 密钥、订阅、队列、派发、账户页设备管理、Service Worker 与 manifest | 已部署（c39b04a） | 真机（Chrome/Android、iOS 主屏幕 web app、macOS Safari）收到与点击须人工验证；密钥轮换未做；`docs/platform/web-push.md` 是权威记录；脚本 `ui_e2e_push.py` |
| CI-31 | 账户侧 Docs 链接：通知链接主机修正（注册表地址）+ 消息里的按读者解析链接卡片 | 已部署（0c73abb） | 脚本 `ui_e2e_link_cards.py`；下一项：评论/提及或 Web Push 研究 |
| CI-30 | 手机宽度复查：复制对话框、来源行、生成对话框、Docs 内铃铛（两引擎）；收件箱抽屉工具条改紧凑 | 已部署（2d77f08） | 脚本 `ui_e2e_phone_copy.py`；下一项：评论/提及（9.8 协作）或 Web Push 研究 |
| CI-29 | Docs 编辑安全与结果通知（设置修订冲突、任务/拒绝收件箱事件、副本来源行） | 已部署（cd90aa3） | 评论/提及与其它 Docs 事件另批；`docs/platform/docs-edit-safety.md` 是权威记录；脚本 `ui_e2e_docs_upgrades.py` |
| CI-28 | 自有界面只写能力名（词表 + 守卫 + 例外表 + PDF 产物元数据） | 已部署（8ac8e55） | 人机验证 iframe 内的供应商品牌、基础模式 PDF 元数据、关于页归属表按记录不改；`docs/platform/component-names.md` 是权威记录；脚本 `ui_e2e_component_names.py` |
候选队列（按风险与价值，2026-09-26 更新；第九节的批次顺序见 docs/platform/notifications.md §7 与 handoff 本表）：
- 运营/账户持有人（CI-24 接续）：SMTP2GO Activity 查 `1xA8Ua-AIkwcC8lNJx-F8I8` 的最终投递；`hikosphere.com` SPF 加 `include:spf.smtp2go.com`；在 SMTP2GO 验证发件域（对齐回信地址与 DKIM）并关闭事务邮件链接/打开跟踪；评估把 From 改到 `wegreen.ltd`（mailcow 已发布其 DKIM）。
第九节余下批次——多格式成员导入（CI-25，已部署）、私聊/群聊（CI-26）、个人项目复制审批（CI-27）均已交付；第三方组件名清理（CI-28）与 Docs 升级（§9.8）待做；
恶意上传防护的后续——`rescan` CLI 与管理端补扫入口、加密压缩包/Office 文件的用户流程（file-acceptance §9）、生产 `clamav`
服务上线后的真实引擎复核；登出落点差异——CI-19 中 Chromium 登出后到 `/login`、WebKit 到 `/logged-out`（两者 `/api/auth/me`
都 401），核对是登出重定向的竞态还是引擎差异并统一；第七节其余专项——分层限流的多实例契约（API 网关按密钥的 300/分钟仍是单进程内存）、WAF/源站清单与规则观察；
Docs 项目列表页仍加载的共享块 `perm`——2026-09-27 用 source map 归因（verification.md §16.64）：324 KB 中 46.5% 是 `messages.ts`、10.8% 是 `messages.server.ts`（三语言字串目录）、15.5% react-router，其余是共享会话/UI 层，没有误入的视图层代码；再拆只能按语言懒加载目录（每页约省 30 KB gzip），收益不抵风险，暂不做；SDK/CLI 与 Node/n8n 验收脚本
（`apps/docs/sdk/acceptance/python_full_flow.py`、`tests/integration/sdk_acceptance/ts_run.py`）纳入每个里程碑的回归——本轮已在
网关/安全头改动后复跑（结果见 verification.md §16.14）。已完成并移出队列：成员列表 50 行审计（CI-04 补审）、键盘模式（CI-05）。

## 2026-09-25 导航、弹层与移动端轮（最新）

范围与状态见 [progress.md](progress.md) 同名节；缺陷—根因—修复—验证表见 [issues.md](issues.md)。
运行入口：隔离实例配方见 [verification.md](verification.md) §16；`services/identity/scripts/ui_e2e_shell.py`
（账户/门户/Docs 外壳回归，Chromium + WebKit）与 `apps/docs/scripts/ui_e2e_mobile.py`（390×844 主链路）。

接续点（需要人或外部条件）：

1. **Google / Microsoft**：按 [operations.md](../operations.md) §7.1 登记两个 Web 应用（回调逐字
   `https://accounts.wegreen.ltd/auth/external/google/callback`、`…/microsoft/callback`，scopes `openid email profile`），
   在 VPS `/opt/synwevo/runtime.env` 追加变量，`docker compose restart accounts`，然后在后台「登录方式」页点「立即检查连接」，
   再用受控账户走登录页→提供商→账户页。代理不能替代；secret 不进聊天。
2. **生产管理员的 TOTP**：本人登录账户页 → 安全因素 → 绑定：输入口令（近期重认证）→ 扫码或手动录入 → 输入一次
   动态码确认 → 保存恢复码。在这之前，用邮箱验证码或外部提供商登录得到的管理员会话会看到「当前登录只验证了一个
   因素」的提示，输入口令即可解锁管理页；口令登录本身不受影响。
3. **真机**：verification.md §16 的 iOS Safari / Android Chrome 清单；**真实外部邮箱**与**真实业务样本**同节。
4. **生产网关的 WebSocket 升级**：把 `infra/production/nginx.conf` 里的 `map $http_upgrade $connection_upgrade` 与 `location /api/collab/`
   两段复制进 VPS 手工维护的 `/opt/synwevo/config/nginx.conf`，`nginx -t` 后 reload；完成前浏览器端的实时协同在生产回退为
   单人编辑并明确提示（`docs/platform/docs-collaboration.md` §9 的「发布闭环」行）。完成后用两个受控账户复跑
   `ui_e2e_collab.py` 式的手工检查（顶栏「实时协同中」、对方光标）。
5. **真实邮箱客户端**：CI-44 的邮件模板在授权测试收件箱（Gmail / Outlook / Apple Mail，桌面与手机、深色与图片屏蔽）的渲染仍未做；
   代码侧无待办。

**协同（9.13—9.16）队列状态（2026-09-27）**：CI-45…56 已实现、本地/CI 验证并部署（矩阵见 `docs/platform/docs-collaboration.md` §9）。
代码侧没有已知的阻断项；下列条件之一出现时再开新批次，不为维持运转编造改动：

| 触发条件 | 接续动作 |
|---|---|
| 运维把 Upgrade 块加进生产 nginx | 两个受控账户在生产复跑 `ui_e2e_collab.py` 式检查（顶栏「实时协同中」、对方光标、保存版本帧），把 §9「发布闭环」行改为已复查 |
| 产品决定表格需要逐键实时同步（而非保存即合并） | 按 §4 的 (a) 路线评估：单元格级 CRDT 与 `SheetEditor` 状态/撤销栈重写，字段与合并区域的业务不变量 |
| 生产出现多进程/多实例部署 | 房间需粘性路由或进程间转发（§1「部署」）；先做测量再选 |
| 有授权的测试收件箱 / 真机 / CUHK 真实邮箱 | 分别走 verification.md §16.4 的接续清单 |
| 用户反馈协同中的具体缺陷 | 按 issues.md 的 缺陷—根因—修复—验证 表处理 |

## 实现与已关闭缺口

| 范围 | 实际行为与验证 |
|---|---|
| 注册/首次使用 | 本机开放注册，pending 无业务权限；验证邮件绑定目标邮箱，显式激活并设置最终口令，避免抢注口令继承。个人空间事务性幂等；个人/组织/邀请意图接续到正确空间。三语言真实注册和 SSO 通过。 |
| 账户中心 | 显示名、安全头像、语言/时区、邮箱变更、口令、安全因素、会话、个人导出、删除与恢复；邮箱变更链接支持未登录跨认证接续。真实 Chrome 6 项账户流程及三语言 18 项安全检查通过。 |
| 组织/空间 | 独立组织成员和空间成员；创建、邀请/重发/撤销/拒绝、角色、所有权转移、生命周期、头像、限额和审计。组织身份不自动取得全部业务数据。注册/邀请/个人/组织/空间切换浏览器 19 项通过。 |
| 平台治理 | 真实用户/组织/空间治理、注册策略、邮件队列元数据与受控重试、审计；治理权不赋予文档读取权。高风险操作要求近期验证、理由、事务内权限复核。 |
| 管理员 API 台 | `api_platform.manage` 只给平台管理员；空间所有者批准与凭据管理分离。控制台、真实契约、受限调试、凭据轮换/撤销、Webhook 和调用记录复用既有服务层。实际 UI 批准签发→已安装 SDK→真实 HTTP→12 份 DOCX→轮换/撤销拒绝→调用记录关联全部通过。 |
| 三语言/网站 | 共用语言核心、分模块资源、账户偏好持久化、可信跨源语言接续、邮件入队固定语言；公共首页、门户、账户/组织、Docs、后台和 API 台接入。用户文档和机器枚举不翻译。资源/参数完整性与关键浏览器流程通过。 |
| Docs 保持可用 | 沿用引擎、模板、映射、格式、公式和持久任务。真实三语言注册→个人空间→门户→Docs→上传/映射/预览/批量生成，9 份 XLSX 和 3 份预览读回原文、数值、公式及缓存值通过。 |
| 恢复/迁移 | Store v6 兼容迁移不重建用户/空间/密钥；组织撤权 journal、账户终止删除重放、外发隔离。真实归档恢复、认领状态/ID/文件哈希保留通过。 |

独立检查发现并关闭：激活前抢注与并发登录、forgot 并发重复投递、注销/重置后的未消费授权码兑换、
RP 设备撤销未关联中央会话、晚到 RP 回调重建已撤销会话、终止删除在旧备份中复活认证因素、
管理员调试中途降权、旧请求覆盖新账户/空间、旧 401 清除新会话、晚到 Blob 交付旧空间内容、
初始化后导航被 refresh 打断、Uvicorn 访问日志记录授权码/查询个人信息。针对性回归与真实浏览器复验分开保留。

## 本机运行入口与隔离范围

- 门户：`http://portal.localhost:28769`
- 账户：`http://accounts.localhost:28770/login`
- Docs：`http://docweave.localhost:28765`
- 数据：`/private/tmp/synwevo-platform-round/live/data`；本地邮件：`/private/tmp/synwevo-platform-round/live/mail`。

287xx 三进程保留供本机查看，只有本任务合成账户/文档，未使用仓库真实 `data/`。此实例初始化未标记 test，
没有事后改标记；破坏性账户/邀请浏览器验收另用从初始化就标记 test 的隔离实例。
297xx、317xx 验收实例已停止；307xx 干净环境启动实例已自动停止。数据/证据保留，不做删除清理。
PID 以实时 `status` 为准，不能按旧报告 PID 结束进程。

在仓库根维护当前 287xx 实例：

```sh
export SYNWEVO_DATA_DIR=/private/tmp/synwevo-platform-round/live/data
export SYNWEVO_MAIL_MODE=file
export SYNWEVO_MAIL_DIR=/private/tmp/synwevo-platform-round/live/mail
export SYNWEVO_REGISTRATION=open
export SYNWEVO_ACCOUNTS_PORT=28770 SYNWEVO_PORTAL_PORT=28769 SYNWEVO_DOCS_PORT=28765
.venv/bin/python scripts/platform.py status
# 需要重新启动当前实例时：
.venv/bin/python scripts/platform.py restart
# 停止当前实例而保留其数据：
.venv/bin/python scripts/platform.py down
```

完整安装、创建新隔离实例及邮件安全说明见 [operations.md](../operations.md)。`.eml` 包含短期令牌，
只在 0700/0600 私有路径中读取，不写入聊天或日志。没有默认管理员口令，初始管理员沿用交互 CLI 引导。

## 本轮运行证据

证据根 `/private/tmp/synwevo-platform-round/`；不同组有重叠，不能相加为一个总测试数量。
命令入口见 [TESTING.md](../TESTING.md)，代码地图/逐文件 SHA 与阅读程度见
[codebase-review.md](../engineering/codebase-review.md) 和 [review-coverage.json](../engineering/review-coverage.json)。

| 运行范围 | 最终结果、证据和边界 |
|---|---|
| 完整账户+集成套件 | `identity-integration-r2.log/xml`：1055 passed、0 failed、0 skipped，264.29s。首次 1044p/6f 保留在 `identity-integration-final.*`；五项旧 audience 夹具及设备计数缺陷修复后重新全跑。 |
| Docs 完整套件与修复 | `docs-suite.log/xml` 初次 837p/2f/1skip；两项旧夹具适配真实服务登记/上传失败保留语义，`docs-regressions.log/xml` 37p。原有内容/SHA断言保留，未删断言或跳过失败；没有宣称重跑了无关全套。历史 skip 是未选择 LICENSE，见下文。 |
| 前端常规测试 | 根 `npm test --workspaces --if-present`：UI 11、Auth 31、Docs 45、Account 19 检查通过；`final-frontend-tests.log`。请求代次与异步竞态采用确定性测试设施，真实浏览器见下方，二者不混称。 |
| 邮件 | `mail-locales.log/xml`：61p；本地 TLS SMTP 接收器、file sink、三语言模板、失败与限流；不证明外部投递或收件箱。 |
| 组织/恢复/迁移 | `org-extras.*` 25p；`restore-final.*` 73p；`account-restore.*` 2p；`org-restore.*` 9p；`migration-v6.*` 2p。真实归档和重复迁移，保留 ID/归属/文件哈希，不复活会话或外发。 |
| 会话与回调 | `rp-late-final.*` 25p；OIDC 真实兑换后注入登出/重置/过期，晚回调拒绝并验证正向设备关联。设备/SSO专项 94p 亦在聚合回归覆盖。 |
| 访问日志 | `access-log-before.log` 保留发现查询泄漏的失败；`access-log-final.log/xml` 15p，真实 Uvicorn 请求验证日志有路径/状态但无合成授权码、邮箱及查询串。 |
| 本机启动 | `local-runtime.log/xml` 38p；双回环真实监听、端口冲突保护。 |
| 干净副本 | `clean-source-manifest.json` 逐文件哈希；新 Python3.12.6 环境依锁安装、pip check、六包从副本导入；Node24.14.0 最终 `npm ci --offline`、三前端 typecheck/build、上述全部前端测试通过（`clean-final-*.log`）；307xx 源站真实 READY 后停止。未复制 data、配置秘密或依赖目录。 |
| 组织/邀请最终浏览器 | `task-f-browser-r5/report.json`：19检查，0失败/0pageerror，407请求；三语言注册/激活/组织、移动390px、组织头像、既有/新用户邀请、拒绝、个人幂等、明确空间SSO/语言刷新。 |
| 账户最终浏览器 | `/private/tmp/synwevo-account-browser-proof-dt_1up9u/report.json`：6流程；邮箱变更未登录接续、旧会话撤销、资料/头像/偏好、个人导出/删除/恢复，无pageerror。账户由隔离身份服务夹具建立；公开注册由上/下两项证明。 |
| 三语言安全最终浏览器 | `security-browser-r4/report.json`：18检查，0失败/0pageerror；真实公开注册、file-mail激活、虚拟 UV CTAP2 Passkey登记/改名/登录/移除、TOTP错误与正确值、恢复码单次消费与重复拒绝。不是物理设备证明。 |
| Docs 最终全链路浏览器 | `docs-browser-final-r2/results.json`：最终请求代次及SSO导航代码下，真实注册→验证→个人→门户→Docs；三语言各3份XLSX与1份预览，原文/公式`=B4*2`/数值12.5、0、-3.25/缓存25、0、-6.5均读回一致；所有修改与预览/ZIP有workspace头，无Docs错误或pageerror。 |
| API 台最终 SDK 浏览器 | `api-platform-evidence/browser-sdk-live/evidence.json`：UI申请/所有者批准/签发，已安装 Python SDK0.1.0 经44次真实本地HTTP请求、无Cookie，程序定义5字段/5映射，预检12有效、真实预览无新任务、12份DOCX/ZIP逐份读回及SHA校验。39条成功调用匹配服务端request ID。Hans轮换后旧key的项目/下载401；Hant撤回后新key同样401；普通owner页面/API404；注销POST200、旧me401。三语言契约搜索/分页通过，0pageerror/外部请求。 |
| API 后端专项 | `api-platform-evidence/` 原83项回归含44竞态、2种凭据引擎流水线与受限控制面；原流水线入站为ASGI TestClient，签名Webhook接收为真实回环服务。只有上项称真实SDK socket全链路。 |

Docs 最终自动操作观测：Chrome153、1600×1000、Apple Silicon、本地合成3行，三语言分别4.34/4.26/4.25秒；
不是人类完成时间、线上性能或前后改善幅度。截图、请求元数据及实际产物 SHA 与 JSON 同目录。
更早的浏览器驱动假设失败也保留；最终脚本通过实际按钮完成接续，不用手动跳转绕开产品流程。
预期 400/401/403/404 是负面验收，不改成假200。

## 覆盖与尚未验证的边界

当前阅读是相关模块增量审阅，未变文件仅在 SHA 一致时接续，未声称重新完整阅读全仓。
覆盖清单区分 full/partial/indexed/excluded；partial/indexed 不能算已完整阅读。历史完整审阅不是本轮全部页面/格式验收。
临时证据可能被系统清理；仓库保留可复现脚本与结果说明，数据库、邮件、凭据、产物不进 Git。

以下是准确的交付边界，不作为已通过项：

- 本轮使用合成文档和本地邮件设施；物理 Passkey、真实SMTP最终收件箱、用户真实复杂模板未验证。上一轮SMTP结果不冒充本轮三语言收件箱证据。
- 关键流程、移动布局和语言资源已验收；没有对每一页面/每一句译文完成独立人工语言审校，也没有全浏览器/低配设备长稳或正式无障碍、安全认证。
- 下载仍先缓冲浏览器 Blob；超大ZIP的流式授权下载是已记录的性能边界，不冒充无限规模支持。
- CLI/其他语言客户端/n8n 的既有能力未删除；本轮新增控制台与安装后PythonSDK已实跑，没有重新宣称所有第三方集成都完成本轮端到端验收。
- LICENSE 尚待权利人选择，历史许可证测试因此skip；发布前需确认依赖许可义务。运营主体、隐私/服务文本的适用法律资料也需运营方确认，页面不编造企业地址或认证。
- Linux生产部署、外部云工作流与生产日志代理配置不在本轮操作范围；不因本机通过而宣称生产已验收。

本轮未留下已知阻断主链路的失败。后续如获部署授权，先复核生产备份、实际邮件/可信URL、schema迁移和平台策略，
再按既有受控流程部署；本轮不触发该动作。
